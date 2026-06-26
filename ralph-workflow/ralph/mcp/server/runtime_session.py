"""Session implementations for the Ralph MCP server.

Provides FileBackedSession (backed by a JSON file written by the parent
Ralph process) and session_from_env (reads session state from environment
variables).
"""

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

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    profile_from_payload,
    resolve_capability_profile,
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


class FileBackedSession:
    """Session view backed by a JSON file updated by the parent Ralph process."""

    def __init__(
        self,
        path: Path,
        *,
        loader: Callable[[Path], dict[str, object]] | None = None,
        session_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        env_getter: Callable[[str], str | None] | None = None,
    ) -> None:
        self._path = path
        self._loader = loader or _load_session_payload
        self._session_id_factory = session_id_factory or (
            lambda: f"standalone-{uuid.uuid4().hex[:8]}"
        )
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))
        self._env_getter = env_getter if env_getter is not None else os.environ.get
        self._media_manifest = MediaManifest()
        self._created_at = time.time()
        # wt-024 M3 (AC-06): parsed-payload cache keyed on (st_mtime_ns,
        # st_size). The 17 session-view accessors all call _load() so the
        # previous implementation re-read + re-parsed the JSON on every
        # property access. With the cache, only the first access in a
        # generation pays the parse cost; subsequent accessors reuse the
        # cached dict. The parent writes via atomic temp+rename so both
        # st_mtime_ns and st_size change on every update — the cache can
        # never serve a stale payload in production.
        self._cache_key: tuple[int, int] | None = None
        self._cached_payload: dict[str, object] | None = None
        # Streaming surface mirroring AgentSession: the exec SSE path swaps the
        # atomic (owner thread, sink) entry per request and the exec handler
        # captures it via current_thread_tool_output_sink. Production servers
        # run with THIS class (via session_from_env), so it must carry the full
        # session surface — enforced statically by session_from_env's McpSession
        # return type and at runtime by
        # tests/test_mcp_server_file_backed_session_agent_session_conformance.py.
        self.tool_output_sink_entry: ToolOutputSinkEntry | None = None

    def current_thread_tool_output_sink(self) -> Callable[[dict[str, object]], None] | None:
        """Return the sink only when the calling thread owns it (single atomic read)."""
        entry = self.tool_output_sink_entry
        if entry is None:
            return None
        owner, sink = entry
        if owner is None or owner == threading.get_ident():
            return sink
        return None

    def _load(self) -> dict[str, object]:
        """Return the cached parsed payload when (mtime_ns, size) is unchanged.

        Otherwise re-invoke ``self._loader`` and refresh the cache. If the
        stat() call fails (file missing or unreadable), the cache is
        discarded and the loader is invoked directly so the calling
        accessor surfaces a normal FileNotFoundError rather than a stale
        cached payload.
        """
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

    @property
    def _workspace_root(self) -> Path:
        return self._path.parent.parent.parent.resolve()

    @property
    def session_id(self) -> str:
        value = self._load().get("session_id", self._session_id_factory())
        return value if isinstance(value, str) else self._session_id_factory()

    @property
    def run_id(self) -> str:
        value = self._load().get("run_id", self._run_id_factory())
        return value if isinstance(value, str) else self._run_id_factory()

    @property
    def drain(self) -> str:
        value = self._load().get("drain", "standalone")
        return value if isinstance(value, str) else "standalone"

    @property
    def capabilities(self) -> set[str]:
        capabilities_value: object = self._load().get("capabilities", [])
        if not isinstance(capabilities_value, list):
            return set()
        return {item for item in capabilities_value if isinstance(item, str)}

    @property
    def worker_artifact_dir(self) -> Path | None:
        """Return worker artifact dir from environment variable.

        For parallel workers, the parent process sets WORKER_ARTIFACT_DIR
        in the subprocess environment. This property reads that value so that
        artifact submission can route to the correct per-worker namespace.
        """
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
    def allowed_roots(self) -> tuple[Path, ...]:
        payload_raw = self._load().get("allowed_roots")
        if not isinstance(payload_raw, list):
            return ()
        return tuple(Path(item).resolve() for item in payload_raw if isinstance(item, str))

    @property
    def model_identity(self) -> MultimodalModelIdentity:
        raw = self._load().get("model_identity")
        if not isinstance(raw, dict):
            return UNKNOWN_IDENTITY
        provider = str(raw.get("provider", "unknown"))
        model_id = raw.get("model_id")
        transport = raw.get("transport")
        return MultimodalModelIdentity(
            provider=provider,
            model_id=str(model_id) if model_id is not None else None,
            transport=str(transport) if transport is not None else None,
        )

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


def session_from_env(
    env: Mapping[str, str] | None = None,
    *,
    session_id_factory: Callable[[], str] | None = None,
    run_id_factory: Callable[[], str] | None = None,
) -> McpSession | None:
    """Load optional session metadata from the environment.

    Returns the structural :class:`McpSession` protocol — NOT a cast. mypy
    verifies both return branches (``FileBackedSession`` and ``AgentSession``)
    against the full session contract, so a public member added to one
    implementation but not the other is a type error in ``make verify``, not a
    production AttributeError.
    """
    env_map = os.environ if env is None else env
    session_file = env_map.get(SESSION_FILE_ENV)
    if session_file:
        return FileBackedSession(
            Path(session_file),
            session_id_factory=session_id_factory,
            run_id_factory=run_id_factory,
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
        provider = str(raw_identity.get("provider", "unknown"))
        model_id_raw = raw_identity.get("model_id")
        transport_raw = raw_identity.get("transport")
        model_identity = MultimodalModelIdentity(
            provider=provider,
            model_id=str(model_id_raw) if model_id_raw is not None else None,
            transport=str(transport_raw) if transport_raw is not None else None,
        )
    else:
        model_identity = UNKNOWN_IDENTITY
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
        parallel_worker=bool(payload.get("parallel_worker", False)),
        worker_artifact_dir=worker_artifact_dir,
        worker_namespace=worker_namespace,
        allowed_roots=allowed_roots,
        model_identity=model_identity,
        stored_capability_profile=stored_profile,
    )
