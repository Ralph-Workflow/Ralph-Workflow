"""Tests for the conflict-resolution pipeline's terminal outcome evidence.

This module covers the typed ``ResolutionOutcome`` surface and the
strategy-rotation session rules: how an invoker-returned failure cause
propagates into the outcome, and why rotating strategies must not
replenish the round budget or reset candidate state that the failure
ledger still needs.

Every test injects the round runner and the git seams, so nothing here
launches a process or touches a repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession, begin_resolution_stop
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py", "docs/beta.md"]
_POLICY_BUNDLE = load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")
_CONFIG = UnifiedConfig.model_validate({"general": {}})

pytestmark = pytest.mark.timeout_seconds(5)


@pytest.fixture(autouse=True)
def _no_real_cooldown_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed agent is benched and the driver waits out its cooldown; never for real here."""
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)


def _policy_bundle() -> PolicyBundle:
    """Return the preloaded default policy that declares the resolution drain."""
    return _POLICY_BUNDLE


def _config() -> UnifiedConfig:
    return _CONFIG


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str],
    surviving_per_round: Sequence[Sequence[str]],
) -> list[Sequence[str]]:
    """Stub the two git queries the driver's verdict rests on."""
    scans: list[Sequence[str]] = []
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    monkeypatch.setattr(
        driver_module,
        "render_conflict_prompt",
        lambda *, root, **_kwargs: root / "PROMPT.md",
    )
    monkeypatch.setattr(driver_module, "_restore_status_bar", lambda *_args: None)

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        index = min(len(scans), len(surviving_per_round) - 1)
        scans.append(paths)
        return list(surviving_per_round[index])

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
    return scans


def _run_outcome(
    tmp_path: Path,
    *,
    invoke: driver_module.ResolutionInvoker,
    session: ResolutionSession | None = None,
    strategy_history: Sequence[str] = (),
) -> ResolutionOutcome:
    return driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=invoke,
        session=session,
        strategy_history=strategy_history,
    )


def test_invoker_returning_outcome_agent_failure_propagates_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[_CONFLICTED])
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> ResolutionOutcome:
        return ResolutionOutcome(
            succeeded=False,
            reason=ResolutionTerminationReason.TOOL_SURFACE_DEAD,
            duration_seconds=1.0,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=tuple(_CONFLICTED),
            failure_cause=ResolutionFailureCause.AGENT_FAILURE,
        )

    outcome = _run_outcome(tmp_path, invoke=_invoke)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.AGENT_FAILURE


def test_invoker_returning_outcome_context_failure_propagates_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[_CONFLICTED])
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> ResolutionOutcome:
        return ResolutionOutcome(
            succeeded=False,
            reason=ResolutionTerminationReason.EXCEPTION,
            duration_seconds=1.5,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=tuple(_CONFLICTED),
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )

    outcome = _run_outcome(tmp_path, invoke=_invoke)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.CONTEXT_FAILURE


def test_invoker_returning_outcome_success_has_cause_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> ResolutionOutcome:
        return ResolutionOutcome(
            succeeded=True,
            reason=None,
            duration_seconds=0.5,
            last_activity_kind="done",
            last_activity_at=100.0,
            unresolved_paths=(),
            failure_cause=ResolutionFailureCause.NONE,
        )

    outcome = _run_outcome(tmp_path, invoke=_invoke)
    assert outcome.succeeded is True
    assert outcome.failure_cause == ResolutionFailureCause.NONE
    assert outcome.unresolved_paths == ()


def test_strategy_rotation_preserves_spent_budget_and_candidate_state() -> None:
    session = ResolutionSession()
    session.rounds_spent = 3
    session.chain_cursor = 1
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE

    begin_resolution_stop(session, is_strategy_rotation=True)
    assert session.rounds_spent == 3
    assert session.chain_cursor == 1
    assert session.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE

    begin_resolution_stop(session, is_strategy_rotation=False)
    assert session.rounds_spent == 0
    assert session.chain_cursor == 0
    assert session.last_failure_cause == ResolutionFailureCause.NONE


def test_strategy_rotation_does_not_replenish_driver_round_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED],
    )
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _s: None)

    rounds_called: list[int] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        rounds_called.append(round_index)
        return False

    session = ResolutionSession(max_rounds_per_stop=2)
    outcome1 = _run_outcome(tmp_path, invoke=_invoke, session=session)
    assert outcome1.succeeded is False
    assert session.rounds_spent == 7
    assert rounds_called == [1, 1, 1, 2, 3, 4, 5, 6, 7]

    outcome2 = _run_outcome(
        tmp_path,
        invoke=_invoke,
        session=session,
        strategy_history=("primary-strategy",),
    )
    assert outcome2.succeeded is False
    assert rounds_called == [1, 1, 1, 2, 3, 4, 5, 6, 7]
    assert session.rounds_spent == 7
