"""A round stops spending candidates once the worktree proves the conflict resolved.

A resolver killed for inactivity AFTER repairing every marker still
repaired every marker. Before the re-scan the round charged that kill as a
failed candidate and prompted the next one on a conflict that no longer
existed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from tests._conflict_resolution_phase_parity_seams import _config, _install_seams, _policy_bundle

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_a_bad_exit_after_a_repaired_worktree_spends_no_further_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[[], []])
    lines: list[str] = []
    monkeypatch.setattr(
        driver_module, "emit_conflict_phase_line", lambda _display, line: lines.append(line)
    )
    session = ResolutionSession()
    invoked: list[str] = []

    def _killed_after_repairing(agent_name: str, *_args: object) -> bool:
        invoked.append(agent_name)
        session.last_attempt_saw_activity = True
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_killed_after_repairing,
            session=session,
        )
        is True
    )
    assert invoked == ["one"], "the repaired worktree must not cost the next candidate"
    assert any("every marker repaired" in line for line in lines)


def test_a_bad_exit_with_markers_left_still_hands_over_to_the_next_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests._conflict_resolution_phase_parity_seams import _CONFLICTED

    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED] * 8)
    session = ResolutionSession(max_rounds_per_stop=1)
    invoked: list[str] = []

    def _killed_mid_work(agent_name: str, *_args: object) -> bool:
        invoked.append(agent_name)
        session.last_attempt_saw_activity = True
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_killed_mid_work,
            session=session,
        )
        is False
    )
    assert invoked[0] == "one"
    assert "two" in invoked
