from __future__ import annotations

import json
import threading
from contextlib import suppress
from pathlib import Path

from ralph.workspace._shared_awareness_error import SharedAwarenessError
from ralph.workspace._shared_awareness_state import SharedAwarenessState

SIDECAR_RELPATH = ".agent/.workspace-awareness.json"
SIDECAR_TEMP_SUFFIX = ".tmp"

_MAX_PUBLISHED_PATHS = 512

_POLL_NEVER = -1
_CLAIM_NEVER = 0


def _document_int(document: dict[str, object], key: str, default: int = 0) -> int:
    value = document.get(key, default)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _document_str(document: dict[str, object], key: str, default: str = "") -> str:
    value = document.get(key, default)
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _document_str_list(document: dict[str, object], key: str) -> list[str]:
    value = document.get(key, [])
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _document_bool(document: dict[str, object], key: str, default: bool = False) -> bool:
    value = document.get(key, default)
    if isinstance(value, bool):
        return value
    return default


class SharedAwarenessSidecar:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.absolute()
        self._path = self._workspace_root / SIDECAR_RELPATH
        self._lock = threading.Lock()
        self._owner_id: str | None = None
        self._epoch = 0
        self._paths: list[str] = []
        self._overflowed = False
        self._polled_epoch = _POLL_NEVER
        self._last_claimed_epoch = _CLAIM_NEVER

    def begin_ownership(self, owner_id: str, *, prior_holder: str | None) -> int:
        with self._lock:
            if prior_holder is not None and prior_holder != owner_id:
                document = self._read_document()
                if document.get("owner_id") == prior_holder:
                    self._owner_id = prior_holder
                    self._epoch = _document_int(document, "epoch")
                    self._paths = _document_str_list(document, "paths")[:_MAX_PUBLISHED_PATHS]
                    self._overflowed = _document_bool(document, "overflowed")
                else:
                    self._reset_locked()
            else:
                self._reset_locked()
            self._owner_id = owner_id
            self._epoch += 1
            self._publish_locked()
            return self._epoch

    def publish_changes(self, paths: list[str], *, overflowed: bool = False) -> int:
        with self._lock:
            if self._owner_id is None:
                msg = "publish_changes requires begin_ownership (watch-lock holder) first"
                raise SharedAwarenessError(msg)
            seen = set(self._paths)
            for path in paths:
                if path not in seen:
                    seen.add(path)
                    self._paths.append(path)
            self._overflowed = self._overflowed or overflowed
            if len(self._paths) > _MAX_PUBLISHED_PATHS:
                del self._paths[:-_MAX_PUBLISHED_PATHS]
                self._overflowed = True
            self._epoch += 1
            self._publish_locked()
            return self._epoch

    def publish_error(self, cause: str) -> None:
        with self._lock:
            if self._owner_id is None:
                return
            self._epoch += 1
            self._write_document(
                {
                    "owner_id": self._owner_id,
                    "epoch": self._epoch,
                    "paths": [],
                    "overflowed": True,
                    "error": cause,
                }
            )

    def end_ownership(self) -> None:
        with self._lock:
            self._owner_id = None
            self._epoch = 0
            self._paths = []
            self._overflowed = False

    def poll(self) -> SharedAwarenessState:
        with self._lock:
            document = self._read_document()
            epoch = _document_int(document, "epoch")
            error = document.get("error")
            if error is not None:
                raise SharedAwarenessError(str(error))
            changed = epoch > self._last_claimed_epoch
            self._polled_epoch = epoch
            return SharedAwarenessState(
                epoch=epoch,
                paths=_document_str_list(document, "paths"),
                overflowed=_document_bool(document, "overflowed"),
                owner_id=_document_str(document, "owner_id", "unknown"),
                changed=changed,
            )

    def claim_epoch(self, epoch: int) -> None:
        with self._lock:
            self._last_claimed_epoch = max(epoch, self._last_claimed_epoch)

    def _reset_locked(self) -> None:
        self._owner_id = None
        self._epoch = 0
        self._paths = []
        self._overflowed = False

    def _publish_locked(self) -> None:
        self._write_document(
            {
                "owner_id": self._owner_id,
                "epoch": self._epoch,
                "paths": list(self._paths),
                "overflowed": self._overflowed,
            }
        )

    def _write_document(self, document: dict[str, object]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._path.with_name(self._path.name + SIDECAR_TEMP_SUFFIX)
            temp_path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            temp_path.replace(self._path)
        except OSError as exc:
            raise SharedAwarenessError(str(exc)) from exc

    def _read_document(self) -> dict[str, object]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise SharedAwarenessError(str(exc)) from exc
        try:
            document: object = json.loads(text)
        except ValueError as exc:
            raise SharedAwarenessError(f"corrupt awareness sidecar: {exc}") from exc
        if not isinstance(document, dict):
            raise SharedAwarenessError("corrupt awareness sidecar: not a JSON object")
        return document

    @property
    def epoch(self) -> int:
        with self._lock:
            return max(self._epoch, self._polled_epoch)


_sidecars: dict[str, SharedAwarenessSidecar] = {}
_sidecars_lock = threading.Lock()


def is_sidecar_path(workspace_root: Path | str, path: str) -> bool:
    sidecar = Path(workspace_root).resolve(strict=False) / SIDECAR_RELPATH
    candidate = Path(path).resolve(strict=False)
    return candidate in (sidecar, sidecar.with_name(sidecar.name + SIDECAR_TEMP_SUFFIX))


def shared_awareness_for_workspace(workspace_root: Path) -> SharedAwarenessSidecar:
    key = str(workspace_root.absolute())
    with _sidecars_lock:
        sidecar = _sidecars.get(key)
        if sidecar is None:
            sidecar = SharedAwarenessSidecar(workspace_root)
            _sidecars[key] = sidecar
        return sidecar


def release_shared_awareness(workspace_root: Path) -> None:
    with _sidecars_lock:
        sidecar = _sidecars.pop(str(workspace_root.absolute()), None)
    if sidecar is not None:
        sidecar.end_ownership()


def remove_shared_awareness_sidecar(workspace_root: Path) -> None:
    with suppress(OSError):
        (workspace_root.absolute() / SIDECAR_RELPATH).unlink(missing_ok=True)


__all__ = [
    "SIDECAR_RELPATH",
    "SIDECAR_TEMP_SUFFIX",
    "SharedAwarenessError",
    "SharedAwarenessSidecar",
    "SharedAwarenessState",
    "is_sidecar_path",
    "release_shared_awareness",
    "remove_shared_awareness_sidecar",
    "shared_awareness_for_workspace",
]
