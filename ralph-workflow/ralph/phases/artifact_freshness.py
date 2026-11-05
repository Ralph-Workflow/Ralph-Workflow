"""Freshness guard: a phase must not be credited with an artifact it never wrote.

Several artifacts (plan, development result, review issues, analysis
decisions, verification evidence) stay on disk between iterations. After a
loopback the previous iteration's file is still there, so a validator that only
asks "does a valid artifact exist?" accepts it even when the agent was killed,
crashed, or exited without submitting. The pipeline then treats the phase as
done instead of charging the agent (which is what drives cooldown/demotion).

The agent-invocation seam records the artifact fingerprint once per invocation
(:func:`record_invocation_baseline`). Phase validators ask
:func:`is_stale_since_invocation`; an artifact identical to that baseline was not
written by the run being validated.

Fail-open by design: with no recorded baseline (a handler driven without the
invocation seam, an overridden artifact path) nothing is reported stale.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.workspace import Workspace


def artifact_fingerprint(absolute_path: Path) -> list[int] | None:
    """Return ``[mtime_ns, size]`` of the artifact file, or None when absent."""
    try:
        st = absolute_path.stat()
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def _baseline_path(phase: str) -> str:
    return f".agent/tmp/artifact_baseline_{phase}.json"


def record_invocation_baseline(workspace: Workspace, phase: str, artifact_path: str) -> None:
    """Remember the artifact as it is BEFORE the agent for ``phase`` runs."""
    with suppress(Exception):
        fingerprint = artifact_fingerprint(Path(workspace.absolute_path(artifact_path)))
        payload: dict[str, object] = {"path": artifact_path, "fingerprint": fingerprint}
        workspace.write(_baseline_path(phase), json.dumps(payload))


def is_stale_since_invocation(workspace: Workspace, phase: str, artifact_path: str) -> bool:
    """True when ``artifact_path`` is unchanged since the invocation began."""
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
    """Retry-hint text for an artifact the agent did not (re)submit."""
    return (
        f"The agent did not submit a new {artifact_type}: {artifact_path} is unchanged "
        "from before this run, so it is a leftover from an earlier iteration. "
        f"Submit the {artifact_type} before declaring completion."
    )
