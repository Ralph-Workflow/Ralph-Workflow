
from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.workspace import Workspace


def artifact_fingerprint(absolute_path: Path) -> list[int] | None:
    try:
        st = absolute_path.stat()
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def _baseline_path(phase: str) -> str:
    return f".agent/tmp/artifact_baseline_{phase}.json"


def record_invocation_baseline(workspace: Workspace, phase: str, artifact_path: str) -> None:
    with suppress(Exception):
        fingerprint = artifact_fingerprint(Path(workspace.absolute_path(artifact_path)))
        payload: dict[str, object] = {"path": artifact_path, "fingerprint": fingerprint}
        workspace.write(_baseline_path(phase), json.dumps(payload))


def is_stale_since_invocation(workspace: Workspace, phase: str, artifact_path: str) -> bool:
    try:
        baseline_file = _baseline_path(phase)
        if not workspace.exists(baseline_file):
            return False
        recorded = cast("dict[str, object]", json.loads(workspace.read(baseline_file)))
        before = recorded["fingerprint"] if recorded["path"] == artifact_path else None
        current = artifact_fingerprint(Path(workspace.absolute_path(artifact_path)))
        return before is not None and before == current
    except Exception:
        return False


def stale_artifact_detail(artifact_path: str, artifact_type: str) -> str:
    return (
        f"The agent did not submit a new {artifact_type}: {artifact_path} is unchanged "
        "from before this run, so it is a leftover from an earlier iteration. "
        f"Submit the {artifact_type} before declaring completion."
    )
