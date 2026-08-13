"""Cross-process shared workspace awareness sidecar (S-2).

A second Ralph process must not register its own recursive watchdog observer
on a workspace already observed by another process. The owning process
publishes one bounded sidecar at ``.agent/.workspace-awareness.json`` so
non-owner processes can consume the owner's change state instead of
duplicating watches.

Sidecar shape (single JSON object, atomically replaced):

* ``owner_id`` — the cross-process watch-lock owner id (``pid:counter``).
* ``epoch`` — monotonically increasing publication counter. A new owner
  always restarts at ``1`` so a stale sidecar from a previous owner can be
  detected after lock acquisition.
* ``paths`` — coalesced workspace-relative source paths (bounded).
* ``overflowed`` — ``True`` when the owner had to drop paths; consumers must
  treat this as a full-reconcile requirement.
* ``error`` — set when the owner's lock/sidecar I/O failed; consumers treat
  this exactly like an unreadable sidecar (bounded live fallback).

Ownership contract: a process may write the sidecar only while it holds the
advisory watch lock (``CrossProcessWatchLock``). Publication therefore needs
no additional serialization — the watch lock IS the write mutex. Every
publication is an atomic ``os.replace`` so a concurrent reader never observes
a torn document. Non-owners poll with ``poll`` and durably claim observed
epochs with ``claim_epoch`` so a crash cannot silently drop owner-published
changes.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import suppress
from pathlib import Path

SIDECAR_RELPATH = ".agent/.workspace-awareness.json"

_MAX_PUBLISHED_PATHS = 512

_POLL_NEVER = -1
_CLAIM_NEVER = 0


class SharedAwarenessError(RuntimeError):
    """Raised when sidecar I/O fails; callers must enter bounded live fallback."""


class SharedAwarenessSidecar:
    """Publish (owner) and poll (non-owner) the workspace awareness sidecar."""

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

    # -- owner lifecycle -------------------------------------------------

    def begin_ownership(self, owner_id: str, *, prior_holder: str | None) -> int:
        """Start a new ownership epoch after acquiring the watch lock.

        A live ``prior_holder`` means the previous owner is still running
        (its id may simply be unreadable); its sidecar content is retained
        and its epoch continues. A stale/absent holder means the previous
        owner exited; the sidecar is reset and the epoch restarts at 1 so
        readers can detect the owner change.
        """
        with self._lock:
            if prior_holder is not None and prior_holder != owner_id:
                document = self._read_document()
                if document.get("owner_id") == prior_holder:
                    self._owner_id = prior_holder
                    self._epoch = int(document.get("epoch", 0))
                    self._paths = [str(p) for p in document.get("paths", [])][:_MAX_PUBLISHED_PATHS]
                    self._overflowed = bool(document.get("overflowed", False))
                else:
                    self._reset_locked()
            else:
                self._reset_locked()
            self._owner_id = owner_id
            self._epoch += 1
            self._publish_locked()
            return self._epoch

    def publish_changes(self, paths: list[str], *, overflowed: bool = False) -> int:
        """Coalesce ``paths`` into the sidecar and bump the epoch (owner only).

        The path set is coalesced by final distinct path and bounded at
        ``_MAX_PUBLISHED_PATHS``; exceeding the bound sets the overflow
        marker so consumers fall back to a full reconcile.
        """
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
        """Record an owner-side I/O failure so consumers enter live fallback."""
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
        """Drop local ownership state; the final sidecar content stays on disk."""
        with self._lock:
            self._owner_id = None
            self._epoch = 0
            self._paths = []
            self._overflowed = False

    # -- non-owner lifecycle ----------------------------------------------

    def poll(self) -> dict[str, object]:
        """Return the owner-published state newer than the last claim.

        Raises ``SharedAwarenessError`` on unreadable/corrupt sidecar so the
        caller can enter bounded live fallback. The returned mapping carries
        ``epoch``, ``paths``, ``overflowed``, ``owner_id``, and optional
        ``error``. An unchanged epoch returns the last claimed state with
        ``changed`` False.
        """
        with self._lock:
            document = self._read_document()
            epoch = int(document.get("epoch", 0))
            if document.get("error"):
                raise SharedAwarenessError(str(document["error"]))
            changed = epoch > self._last_claimed_epoch
            self._polled_epoch = epoch
            return {
                "epoch": epoch,
                "paths": [str(p) for p in document.get("paths", [])],
                "overflowed": bool(document.get("overflowed", False)),
                "owner_id": str(document.get("owner_id", "unknown")),
                "changed": changed,
            }

    def claim_epoch(self, epoch: int) -> None:
        """Durably mark ``epoch`` as consumed so it is not re-reported."""
        with self._lock:
            if epoch > self._last_claimed_epoch:
                self._last_claimed_epoch = epoch

    # -- internals ---------------------------------------------------------

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
            temp_path = self._path.with_name(self._path.name + ".tmp")
            # filesystem-write-ok: atomic shared-awareness sidecar publication.
            temp_path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            os.replace(temp_path, self._path)
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
            document = json.loads(text)
        except ValueError as exc:
            raise SharedAwarenessError(f"corrupt awareness sidecar: {exc}") from exc
        if not isinstance(document, dict):
            raise SharedAwarenessError("corrupt awareness sidecar: not a JSON object")
        return document

    @property
    def epoch(self) -> int:
        """Current local epoch (owner) or last polled epoch (non-owner)."""
        with self._lock:
            return max(self._epoch, self._polled_epoch)


_sidecars: dict[str, SharedAwarenessSidecar] = {}  # bounded-accumulator-ok: release_shared_awareness removes final workspace entry
_sidecars_lock = threading.Lock()


def shared_awareness_for_workspace(workspace_root: Path) -> SharedAwarenessSidecar:
    """Return the canonical process-local sidecar handle for ``workspace_root``."""
    key = str(workspace_root.absolute())
    with _sidecars_lock:
        sidecar = _sidecars.get(key)
        if sidecar is None:
            sidecar = SharedAwarenessSidecar(workspace_root)
            _sidecars[key] = sidecar
        return sidecar


def release_shared_awareness(workspace_root: Path) -> None:
    """Release the process-local sidecar handle (final lease teardown)."""
    with _sidecars_lock:
        sidecar = _sidecars.pop(str(workspace_root.absolute()), None)
    if sidecar is not None:
        sidecar.end_ownership()


def remove_shared_awareness_sidecar(workspace_root: Path) -> None:
    """Best-effort removal of the sidecar file at final teardown."""
    with suppress(OSError):
        (workspace_root.absolute() / SIDECAR_RELPATH).unlink(missing_ok=True)


__all__ = [
    "SIDECAR_RELPATH",
    "SharedAwarenessError",
    "SharedAwarenessSidecar",
    "release_shared_awareness",
    "remove_shared_awareness_sidecar",
    "shared_awareness_for_workspace",
]
