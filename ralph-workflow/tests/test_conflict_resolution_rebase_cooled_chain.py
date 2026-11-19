from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline import resolve_conflicted_rebase
from ralph.pipeline.conflict_resolution.round_verdict import resolution_halt_reason
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ResolutionTerminationReason,
)

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_rebase_all_cooling_resolution_retains_actionable_operator_message (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_rebase_all_spent_resolution_retains_actionable_operator_message with an unchanged body"
    ),
    "test_conflict_resolution_rebase_cooled_chain module": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; module renamed from tests/test_conflict_resolution_rebase_spent_chain.py with unchanged content"
    ),
    "test_rebase_all_cooling_resolution_retains_actionable_operator_message": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    'assert "needs operator action: git add -- app.py" in reason': (
        "production spent-chain exhaustion now names a resume point instead of an "
        "operator demand; the assertion still requires the same git-add command and "
        "NO_RUNNABLE_CANDIDATE retention"
    ),
    'assert "needs operator action: git add -- app.py" in synth_reason': (
        "synthesized exhaustion reasons use the same resume-point wording as production; "
        "the assertion still requires the git-add command for the unresolved path"
    ),
    'assert "needs operator action: git add -- app.py" in short_circuit.last_reason': (
        "short-circuit last_reason carries the resume-point form of the spent-chain "
        "message; the assertion still requires the git-add command and spent-chain facts"
    ),
}


class _SpentChainRunner:
    def __init__(self, action_message: str) -> None:
        self.action_message = action_message

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        del root, target, resolver
        assert session is not None
        session.terminal_reason = ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
        session.unresolved_paths = ("app.py",)
        session.operator_action_message = self.action_message
        session.halt_reason = resolution_halt_reason(session, session.unresolved_paths)
        return False


def test_rebase_all_cooling_resolution_retains_actionable_operator_message(
    tmp_path: Path,
) -> None:
    session = ResolutionSession()
    session.terminal_reason = ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
    session.unresolved_paths = ("app.py",)
    action_msg = (
        "all 2 resolver agents are on cooldown (dev: cooldown unproductive_limit, "
        "review: cooldown unproductive_limit); "
        "conflict on app.py; resume point: git add -- app.py"
    )
    session.operator_action_message = action_msg

    reason = resolution_halt_reason(session, session.unresolved_paths)
    assert action_msg in reason
    assert "resume point: git add -- app.py" in reason

    session_no_msg = ResolutionSession()
    session_no_msg.terminal_reason = ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
    session_no_msg.unresolved_paths = ("app.py",)
    synth_reason = resolution_halt_reason(session_no_msg, session_no_msg.unresolved_paths)
    assert "resume point: git add -- app.py" in synth_reason

    result = resolve_conflicted_rebase(
        tmp_path,
        "main",
        lambda *args: False,
        None,
        None,
        agent_runner=_SpentChainRunner(action_msg),
    )
    assert result is not None
    short_circuit = result.short_circuit
    assert short_circuit is not None
    assert short_circuit.resolution_exhausted is True
    assert short_circuit.last_reason is not None
    assert "resume point: git add -- app.py" in short_circuit.last_reason
    assert "all 2 resolver agents are on cooldown" in short_circuit.last_reason
