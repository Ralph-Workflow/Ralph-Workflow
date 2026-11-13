from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline import resolve_conflicted_rebase
from ralph.pipeline.conflict_resolution.round_verdict import resolution_exhaustion_reason
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ResolutionTerminationReason,
)

if TYPE_CHECKING:
    import pytest

    from ralph.pipeline.conflict_resolution import rebase_loop as loop_module


def test_rebase_all_spent_resolution_retains_actionable_operator_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = ResolutionSession()
    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    session.unresolved_paths = ("app.py",)
    action_msg = (
        "all 2 resolver agents are spent/unavailable (dev: spent, review: spent); "
        "conflict on app.py needs operator action: git add -- app.py"
    )
    session.operator_action_message = action_msg

    reason = resolution_exhaustion_reason(session, session.unresolved_paths)
    assert action_msg in reason
    assert "needs operator action: git add -- app.py" in reason

    session_no_msg = ResolutionSession()
    session_no_msg.terminal_reason = ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    session_no_msg.unresolved_paths = ("app.py",)
    synth_reason = resolution_exhaustion_reason(session_no_msg, session_no_msg.unresolved_paths)
    assert "needs operator action: git add -- app.py" in synth_reason

    from ralph.pipeline import auto_integrate_rebase_merge as merge_mod

    def _fake_resolve_rebase(
        _r: Path,
        _t: str,
        _resolver: loop_module.RebaseStopResolver,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        if session is not None:
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_EXHAUSTED
            session.unresolved_paths = ("app.py",)
            session.operator_action_message = action_msg
            session.exhaustion_reason = resolution_exhaustion_reason(
                session, session.unresolved_paths
            )
        return False

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", _fake_resolve_rebase)

    result = resolve_conflicted_rebase(tmp_path, "main", lambda *args: False, None, None)
    assert result is not None
    short_circuit = result.short_circuit
    assert short_circuit is not None
    assert short_circuit.resolution_exhausted is True
    assert short_circuit.last_reason is not None
    assert "needs operator action: git add -- app.py" in short_circuit.last_reason
    assert "all 2 resolver agents are spent/unavailable" in short_circuit.last_reason
