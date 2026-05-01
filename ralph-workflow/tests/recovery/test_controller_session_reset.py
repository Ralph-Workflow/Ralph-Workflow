"""Black-box tests: controller clears stale session state on reset_session failure."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _AgentInvocationError(Exception):
    """Simulates AgentInvocationError via class name."""


_AgentInvocationError.__name__ = "AgentInvocationError"


def _make_state(
    agents: list[str],
    last_session_id: str | None = None,
    session_preserve: bool = False,
) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
        last_agent_session_id=last_session_id,
        session_preserve_retry_pending=session_preserve,
    )


def test_stale_session_clears_last_agent_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stale-session failure, last_agent_session_id is cleared."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"], last_session_id="deadbeef-1234", session_preserve=True)

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: deadbeef-1234"
    )
    new_state, _, _ = controller.handle(state, exc, phase="development", agent="claude")

    assert new_state.last_agent_session_id is None


def test_stale_session_clears_session_preserve_retry_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stale-session failure, session_preserve_retry_pending is False."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"], last_session_id="deadbeef-1234", session_preserve=True)

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: deadbeef-1234"
    )
    new_state, _, _ = controller.handle(state, exc, phase="development", agent="claude")

    assert new_state.session_preserve_retry_pending is False


def test_stale_session_writes_retry_hint_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stale-session failure, a retry hint file exists with relevant content."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"], last_session_id="abc-session")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: abc-session"
    )
    controller.handle(state, exc, phase="development", agent="claude")

    hint_file = tmp_path / ".agent" / "tmp" / f"last_retry_error_{"development"}.txt"
    assert hint_file.exists(), "Retry hint file should be written on stale-session failure"
    content = hint_file.read_text(encoding="utf-8")
    assert "session" in content.lower()
    assert "No conversation found with session ID" in content


def test_stale_session_debits_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale-session failure still decrements the agent retry budget."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"], last_session_id="stale-id")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: stale-id"
    )
    _, _, evt = controller.handle(state, exc, phase="development", agent="claude")

    assert evt.counted_against_budget is True
    budget = controller.budget_registry.get("development", "claude")
    assert budget is not None
    assert budget.consumed == 1


def test_stale_session_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stale-session failure, the pipeline remains in the current phase for retry."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"], last_session_id="stale-id")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: stale-id"
    )
    new_state, effects, _ = controller.handle(
        state, exc, phase="development", agent="claude"
    )

    assert new_state.phase == "development"
    assert effects == []
