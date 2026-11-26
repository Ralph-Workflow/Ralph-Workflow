from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from ralph.project_policy import markers

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _policy_scope_paths(workspace: Workspace) -> list[str]:
    try:
        entries = workspace.list_dir(markers.CANONICAL_DIR.rstrip("/"))
    except (OSError, ValueError):
        entries = []
    paths = [f"{markers.CANONICAL_DIR}{entry.rsplit('/', 1)[-1]}" for entry in entries]
    paths.extend(name for name in (markers.AGENTS_MD, markers.CLAUDE_MD) if workspace.exists(name))
    return sorted(set(paths))


def _read_record(workspace: Workspace) -> dict[str, str]:
    if not workspace.exists(markers.POLICY_REMEDIATION_WRITES_REL_PATH):
        return {}
    try:
        payload: object = json.loads(workspace.read(markers.POLICY_REMEDIATION_WRITES_REL_PATH))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    record = cast("dict[str, object]", payload)
    return {path: digest for path, digest in record.items() if isinstance(digest, str)}


def _current_digest(workspace: Workspace, path: str) -> str | None:
    try:
        return _digest(workspace.read(path))
    except (OSError, ValueError):
        return None


def without_ralph_authored(workspace: Workspace, pre_run_dirty: frozenset[str]) -> frozenset[str]:
    record = _read_record(workspace)
    if not record:
        return pre_run_dirty
    return frozenset(
        path for path in pre_run_dirty if record.get(path) != _current_digest(workspace, path)
    )


def record_remediation_writes(workspace: Workspace, user_owned: frozenset[str]) -> None:
    record: dict[str, str] = {}
    for path in _policy_scope_paths(workspace):
        if path in user_owned:
            continue
        digest = _current_digest(workspace, path)
        if digest is not None:
            record[path] = digest
    try:
        workspace.write(markers.POLICY_REMEDIATION_WRITES_REL_PATH, json.dumps(record, indent=1))
    except (OSError, ValueError):
        return
