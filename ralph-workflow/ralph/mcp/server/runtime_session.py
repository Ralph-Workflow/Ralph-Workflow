from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.mcp.tools._exec_resource_protocol import ExecResourceResolverLike

from ralph.mcp.explore.handlers import build_explore_index
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    caller_identity_for,
    caller_profile_for,
    identity_on_transport,
    payload_identity,
    payload_transport,
    profile_from_payload,
    resolve_capability_profile,
    transport_inline_image_roundtrip_unsafe,
)
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.protocol.env import (
    MCP_SESSION_ENV as SESSION_ENV,
)
from ralph.mcp.protocol.env import (
    MCP_SESSION_FILE_ENV as SESSION_FILE_ENV,
)
from ralph.mcp.protocol.env import (
    WORKER_ARTIFACT_DIR_ENV as WORKER_ARTIFACT_DIR,
)
from ralph.mcp.protocol.session import (
    AgentSession,
    McpSession,
    ToolOutputSinkEntry,
    session_has_capability,
)
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._snapshots import read_snapshot, snapshot_dir
from ralph.mcp.tools._exec_resource_uri import ExecResourceResolver


def reconcile_declared_transport(declared: str | None, persisted: object) -> str | None:
    stated = payload_transport(persisted)
    declared_clean = payload_transport(declared)
    for candidate in (declared_clean, stated):
        if candidate is not None and transport_inline_image_roundtrip_unsafe(candidate):
            return candidate
    return declared_clean or stated


def session_identity_from_payload(
    raw_identity: Mapping[str, object],
    declared: str | None,
) -> MultimodalModelIdentity:
    stated_transport = payload_transport(raw_identity.get("transport"))
    resolved = reconcile_declared_transport(declared, raw_identity.get("transport"))
    stated = payload_identity(
        raw_identity.get("provider"), raw_identity.get("model_id"), stated_transport
    )
    if resolved is None or stated_transport == resolved:
        return identity_on_transport(stated, resolved)
    return MultimodalModelIdentity(
        provider=UNKNOWN_IDENTITY.provider, model_id=None, transport=resolved
    )


class FileBackedSession:
    def __init__(
        self,
        path: Path,
        *,
        loader: Callable[[Path], dict[str, object]] | None = None,
        session_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        env_getter: Callable[[str], str | None] | None = None,
        broker_secret: str | None = None,
        exec_resource_resolver: ExecResourceResolverLike | None = None,
        exec_spill_roots: tuple[Path, ...] | None = None,
        declared_agent_transport: str | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._declared_agent_transport = (declared_agent_transport or "").strip().lower() or None
        self._path = path
        self._explicit_workspace_root = (
            workspace_root.resolve() if workspace_root is not None else None
        )
        self._loader = loader or _load_session_payload
        self._session_id_factory = session_id_factory or (
            lambda: f"standalone-{uuid.uuid4().hex[:8]}"
        )
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))
        self._env_getter = env_getter if env_getter is not None else os.environ.get
        self._broker_secret: str | None = broker_secret
        self._media_manifest = MediaManifest()
        self._created_at = time.time()
        self._cache_key: tuple[int, int] | None = None
        self._cached_payload: dict[str, object] | None = None
        self.tool_output_sink_entry: ToolOutputSinkEntry | None = None
        self.explore_index: object | None = self._lazy_build_explore_index()
        self._exec_resource_resolver: ExecResourceResolverLike | None = exec_resource_resolver
        if exec_spill_roots is not None:
            self._exec_spill_roots: tuple[Path, ...] | None = tuple(exec_spill_roots)
        else:
            self._exec_spill_roots = self._discover_exec_spill_roots_from_disk()

    def _lazy_build_explore_index(self) -> object | None:
        try:
            workspace_root = self._workspace_root
        except Exception:
            return None
        try:
            handle = build_explore_index(workspace_root)
        except Exception:
            return None
        return handle

    def current_thread_tool_output_sink(self) -> Callable[[dict[str, object]], None] | None:
        entry = self.tool_output_sink_entry
        if entry is None:
            return None
        owner, sink = entry
        if owner is None or owner == threading.get_ident():
            return sink
        return None

    def _load(self) -> dict[str, object]:
        try:
            stat = self._path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            self._cache_key = None
            self._cached_payload = None
            return self._loader(self._path)
        if self._cache_key == key and self._cached_payload is not None:
            return self._cached_payload
        payload = self._loader(self._path)
        self._cache_key = key
        self._cached_payload = payload
        return payload

    def _discover_exec_spill_roots_from_disk(self) -> tuple[Path, ...] | None:
        try:
            payload_obj: object = self._load()
        except (OSError, ValueError):
            return None
        if not isinstance(payload_obj, dict):
            return None
        raw_roots: object = payload_obj.get("exec_spill_roots")
        if not isinstance(raw_roots, list):
            return None
        collected = [Path(item) for item in raw_roots if isinstance(item, str) and item]
        if not collected:
            return None
        return tuple(collected)

    @property
    def _workspace_root(self) -> Path:
        if self._explicit_workspace_root is not None:
            return self._explicit_workspace_root
        return _resolve_workspace_root(self._path)

    @property
    def exec_resource_resolver(self) -> ExecResourceResolverLike | None:
        if self._exec_resource_resolver is not None:
            return self._exec_resource_resolver
        if not self._exec_spill_roots:
            return None
        self._exec_resource_resolver = ExecResourceResolver(spill_roots=self._exec_spill_roots)
        return self._exec_resource_resolver

    @exec_resource_resolver.setter
    def exec_resource_resolver(self, value: ExecResourceResolverLike | None) -> None:
        self._exec_resource_resolver = value

    @property
    def session_id(self) -> str:
        loaded = self._load()
        value = loaded.get("session_id")
        if isinstance(value, str):
            return value
        run_id_raw = loaded.get("run_id")
        if isinstance(run_id_raw, str):
            try:
                snap_dir = snapshot_dir(self._workspace_root, run_id_raw)
                snap_data = read_snapshot(snap_dir)
            except OSError:
                snap_data = None
            if snap_data:
                snap_session_id = snap_data.get("session_id")
                if isinstance(snap_session_id, str):
                    return snap_session_id
        factory_value = self._session_id_factory()
        if isinstance(factory_value, str) and factory_value:
            return factory_value
        raise McpServerError(
            "session_id missing",
            restart_count=0,
            last_stage="snapshot_missing",
        )

    @property
    def run_id(self) -> str:
        value = self._load().get("run_id", self._run_id_factory())
        return value if isinstance(value, str) else self._run_id_factory()

    @property
    def drain(self) -> str:
        value = self._load().get("drain", "standalone")
        return value if isinstance(value, str) else "standalone"

    @property
    def activity_only_supervision(self) -> bool:
        return bool(self._load().get("activity_only_supervision", False))

    @property
    def capabilities(self) -> set[str]:
        capabilities_value: object = self._load().get("capabilities", [])
        if not isinstance(capabilities_value, list):
            return set()
        return {item for item in capabilities_value if isinstance(item, str)}

    @property
    def worker_artifact_dir(self) -> Path | None:
        raw = self._env_getter(WORKER_ARTIFACT_DIR)
        if raw is not None:
            return Path(raw)
        payload_raw = self._load().get("worker_artifact_dir")
        if isinstance(payload_raw, str) and payload_raw:
            return Path(payload_raw)
        return None

    @property
    def worker_namespace(self) -> Path | None:
        payload_raw = self._load().get("worker_namespace")
        if isinstance(payload_raw, str) and payload_raw:
            return Path(payload_raw)
        return None

    @property
    def broker_secret(self) -> str | None:
        return self._broker_secret

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        payload_raw = self._load().get("allowed_roots")
        if not isinstance(payload_raw, list):
            return ()
        return tuple(Path(item).resolve() for item in payload_raw if isinstance(item, str))

    @property
    def model_identity(self) -> MultimodalModelIdentity:
        raw = self._load().get("model_identity")
        if not isinstance(raw, dict):
            return MultimodalModelIdentity(
                provider=UNKNOWN_IDENTITY.provider,
                model_id=None,
                transport=self._declared_agent_transport,
            )
        return session_identity_from_payload(raw, self._declared_agent_transport)

    @property
    def capability_profile(self) -> ResolvedCapabilityProfile | None:
        raw = self._load().get("capability_profile")
        if isinstance(raw, dict):
            return profile_from_payload(raw)
        return resolve_capability_profile(self.model_identity)

    @property
    def media_manifest(self) -> MediaManifest:
        return self._media_manifest

    @property
    def policy_flags(self) -> set[str] | None:
        payload_raw = self._load().get("policy_flags")
        if not isinstance(payload_raw, list):
            return None
        return {item for item in payload_raw if isinstance(item, str)}

    @property
    def created_at(self) -> float:
        payload_raw = self._load().get("created_at")
        if isinstance(payload_raw, (int, float)) and not isinstance(payload_raw, bool):
            return float(payload_raw)
        return self._created_at

    @property
    def parallel_worker(self) -> bool:
        return self.is_parallel_worker()

    @property
    def edit_area_result(self) -> object:
        return self._load().get("edit_area_result")

    @property
    def stored_capability_profile(self) -> ResolvedCapabilityProfile | None:
        raw = self._load().get("capability_profile")
        if isinstance(raw, dict):
            return profile_from_payload(raw)
        return None

    @property
    def delegated_agent_id(self) -> str | None:
        raw = self._load().get("delegated_agent_id")
        return raw if isinstance(raw, str) and raw else None

    @property
    def delegated_model_identity(self) -> MultimodalModelIdentity | None:
        raw = self._load().get("delegated_model_identity")
        if not isinstance(raw, dict):
            return None
        raw_transport = payload_transport(raw.get("transport"))
        return payload_identity(raw.get("provider"), raw.get("model_id"), raw_transport)

    @property
    def delegated_capability_profile(self) -> ResolvedCapabilityProfile | None:
        raw = self._load().get("delegated_capability_profile")
        return profile_from_payload(raw) if isinstance(raw, dict) else None

    @property
    def caller_agent_id(self) -> str:
        return self.delegated_agent_id or self.session_id

    @property
    def caller_model_identity(self) -> MultimodalModelIdentity:
        return caller_identity_for(self.model_identity, self.delegated_model_identity)

    @property
    def caller_capability_profile(self) -> ResolvedCapabilityProfile:
        return caller_profile_for(
            self.model_identity,
            self.delegated_model_identity,
            self.capability_profile,
            self.delegated_capability_profile,
        )

    def check_capability(self, capability: str) -> object:
        return "approved" if session_has_capability(self.capabilities, capability) else "denied"

    def is_parallel_worker(self) -> bool:
        payload_raw = self._load().get("parallel_worker", False)
        return bool(payload_raw) or self.worker_artifact_dir is not None

    def check_edit_area(self, path: str) -> object:
        if not self.is_parallel_worker():
            return "approved"
        allowed_roots = self.allowed_roots
        if not allowed_roots:
            return "denied"
        try:
            resolved = (self._workspace_root / path).resolve()
        except Exception:
            return "denied"
        if any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
            return "approved"
        return "denied"


def _load_session_payload(path: Path) -> dict[str, object]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must encode an object")
    return raw


def _resolve_workspace_root(path: Path) -> Path:
    resolved_path = Path(path).resolve()
    if resolved_path.parent.name == ".agent":
        return resolved_path.parent.parent.resolve()
    cursor = resolved_path.parent
    while cursor != cursor.parent:
        if (cursor / ".agent").is_dir():
            return cursor.resolve()
        cursor = cursor.parent
    return resolved_path.parent.parent.parent.resolve()


def session_from_env(
    env: Mapping[str, str] | None = None,
    *,
    session_id_factory: Callable[[], str] | None = None,
    run_id_factory: Callable[[], str] | None = None,
    declared_agent_transport: str | None = None,
    workspace_root: Path | None = None,
) -> McpSession | None:
    env_map = os.environ if env is None else env
    broker_secret_value: str | None = env_map.get("RALPH_BROKER_SECRET") or None
    session_file = env_map.get(SESSION_FILE_ENV)
    if session_file:
        return FileBackedSession(
            Path(session_file),
            session_id_factory=session_id_factory,
            run_id_factory=run_id_factory,
            broker_secret=broker_secret_value,
            declared_agent_transport=declared_agent_transport,
            workspace_root=workspace_root,
        )

    raw = env_map.get(SESSION_ENV)
    if not raw:
        return None
    payload_obj: object = json.loads(raw)
    if not isinstance(payload_obj, dict):
        raise ValueError(f"{SESSION_ENV} must encode an object")
    payload: dict[str, object] = payload_obj

    capabilities_value: object = payload.get("capabilities", [])
    capabilities = (
        {item for item in capabilities_value if isinstance(item, str)}
        if isinstance(capabilities_value, list)
        else set()
    )
    raw_identity = payload.get("model_identity")
    if isinstance(raw_identity, dict):
        model_identity = session_identity_from_payload(raw_identity, declared_agent_transport)
    else:
        model_identity = MultimodalModelIdentity(
            provider=UNKNOWN_IDENTITY.provider,
            model_id=None,
            transport=payload_transport(declared_agent_transport),
        )
    raw_profile = payload.get("capability_profile")
    stored_profile = profile_from_payload(raw_profile) if isinstance(raw_profile, dict) else None
    if stored_profile is None and model_identity.is_known():
        stored_profile = resolve_capability_profile(model_identity)
    raw_allowed_roots = payload.get("allowed_roots")
    allowed_roots = (
        tuple(Path(item).resolve() for item in raw_allowed_roots if isinstance(item, str))
        if isinstance(raw_allowed_roots, list)
        else ()
    )
    session_id_value = payload.get("session_id")
    if not isinstance(session_id_value, str):
        session_id_value = (
            session_id_factory()
            if session_id_factory is not None
            else f"standalone-{uuid.uuid4().hex[:8]}"
        )
    run_id_value = payload.get("run_id")
    if not isinstance(run_id_value, str):
        run_id_value = run_id_factory() if run_id_factory is not None else str(uuid.uuid4())
    drain_value = payload.get("drain", "standalone")
    if not isinstance(drain_value, str):
        drain_value = "standalone"
    worker_artifact_value = payload.get("worker_artifact_dir")
    worker_artifact_dir: Path | None = (
        Path(worker_artifact_value) if isinstance(worker_artifact_value, str) else None
    )
    worker_namespace_value = payload.get("worker_namespace")
    worker_namespace: Path | None = (
        Path(worker_namespace_value) if isinstance(worker_namespace_value, str) else None
    )
    return AgentSession(
        session_id=session_id_value,
        run_id=run_id_value,
        drain=drain_value,
        capabilities=capabilities,
        activity_only_supervision=bool(payload.get("activity_only_supervision", False)),
        parallel_worker=bool(payload.get("parallel_worker", False)),
        worker_artifact_dir=worker_artifact_dir,
        worker_namespace=worker_namespace,
        allowed_roots=allowed_roots,
        model_identity=model_identity,
        stored_capability_profile=stored_profile,
        broker_secret=broker_secret_value,
    )
