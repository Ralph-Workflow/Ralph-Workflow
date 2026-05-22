"""Session implementations for the Ralph MCP server.

Provides FileBackedSession (backed by a JSON file written by the parent
Ralph process) and session_from_env (reads session state from environment
variables).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
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
from ralph.mcp.protocol.session import AgentSession, session_has_capability


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

    def _load(self) -> dict[str, object]:
        return self._loader(self._path)

    @property
    def _workspace_root(self) -> Path:
        return self._path.parent.parent.parent.resolve()

    @property
    def session_id(self) -> str:
        return cast("str", self._load().get("session_id", self._session_id_factory()))

    @property
    def run_id(self) -> str:
        return cast("str", self._load().get("run_id", self._run_id_factory()))

    @property
    def drain(self) -> str:
        return cast("str", self._load().get("drain", "standalone"))

    @property
    def capabilities(self) -> set[str]:
        capabilities_value: object = self._load().get("capabilities", [])
        if not isinstance(capabilities_value, list):
            return set()
        return set(cast("list[str]", capabilities_value))

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
    def model_identity(self) -> object:
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
    def capability_profile(self) -> object:
        raw = self._load().get("capability_profile")
        if isinstance(raw, dict):
            return profile_from_payload(raw)
        identity = self.model_identity
        if not isinstance(identity, MultimodalModelIdentity):
            return resolve_capability_profile(UNKNOWN_IDENTITY)
        return resolve_capability_profile(identity)

    @property
    def media_manifest(self) -> object:
        return self._media_manifest

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
    payload = cast("object", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must encode an object")
    return cast("dict[str, object]", payload)


def session_from_env(
    env: Mapping[str, str] | None = None,
    *,
    session_id_factory: Callable[[], str] | None = None,
    run_id_factory: Callable[[], str] | None = None,
) -> AgentSession | None:
    """Load optional session metadata from the environment."""
    env_map = os.environ if env is None else env
    session_file = env_map.get(SESSION_FILE_ENV)
    if session_file:
        return cast(
            "AgentSession",
            FileBackedSession(
                Path(session_file),
                session_id_factory=session_id_factory,
                run_id_factory=run_id_factory,
            ),
        )

    raw = env_map.get(SESSION_ENV)
    if not raw:
        return None
    payload = cast("object", json.loads(raw))
    if not isinstance(payload, dict):
        raise ValueError(f"{SESSION_ENV} must encode an object")

    capabilities_value: object = payload.get("capabilities", [])
    capabilities = (
        set(cast("list[str]", capabilities_value))
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
    return AgentSession(
        session_id=cast(
            "str",
            payload.get(
                "session_id",
                session_id_factory()
                if session_id_factory is not None
                else f"standalone-{uuid.uuid4().hex[:8]}",
            ),
        ),
        run_id=cast(
            "str",
            payload.get(
                "run_id",
                run_id_factory() if run_id_factory is not None else str(uuid.uuid4()),
            ),
        ),
        drain=cast("str", payload.get("drain", "standalone")),
        capabilities=capabilities,
        parallel_worker=bool(payload.get("parallel_worker", False)),
        worker_artifact_dir=(
            Path(cast("str", payload["worker_artifact_dir"]))
            if isinstance(payload.get("worker_artifact_dir"), str)
            else None
        ),
        worker_namespace=(
            Path(cast("str", payload["worker_namespace"]))
            if isinstance(payload.get("worker_namespace"), str)
            else None
        ),
        allowed_roots=allowed_roots,
        model_identity=model_identity,
        stored_capability_profile=stored_profile,
    )
