"""End-to-end tests: stale session failure triggers session reset and context-preserving retry.

These tests operate at two levels:
- RecoveryController / FailureClassifier level (pure logic tests)
- runner._execute_agent_effect level (true E2E: drives the runner with a fake agent)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import PHASE_DEVELOPMENT
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.runner import WorkspaceScope
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# ---------------------------------------------------------------------------
# Helpers shared by controller-level tests
# ---------------------------------------------------------------------------


def _make_state(
    last_session_id: str | None = None,
    session_preserve: bool = False,
) -> PipelineState:
    return PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        last_agent_session_id=last_session_id,
        session_preserve_retry_pending=session_preserve,
    )


def _make_config(agent_idle_timeout_seconds: float = 300.0) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(agent_idle_timeout_seconds=agent_idle_timeout_seconds)
    )


# ---------------------------------------------------------------------------
# Runner-level E2E: drives _execute_agent_effect with fake agent
# ---------------------------------------------------------------------------


class FakeBridge:
    """Minimal MCP bridge stub for runner tests."""

    def shutdown(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:19999/mcp"


def _registry_factory(agent_config: AgentConfig) -> object:
    """Return a registry factory stub that always resolves to agent_config."""

    class _RegistryInstance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class _Registry:
        @classmethod
        def from_config(cls, cfg: object) -> _RegistryInstance:
            del cls, cfg
            return _RegistryInstance()

    return _Registry


def test_runner_stale_session_returns_agent_failure_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_execute_agent_effect returns AGENT_FAILURE when agent raises stale-session error.

    Stale-session errors are not internally retried by _execute_agent_effect; they
    surface as AGENT_FAILURE so the outer state machine can route through RecoveryController.
    """
    monkeypatch.setattr(runner_module, "start_mcp_server", lambda *a, **kw: FakeBridge())
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _: None)
    monkeypatch.setattr(
        runner_module,
        "materialize_system_prompt",
        lambda *, workspace_root, name: str(tmp_path / "SYS.md"),
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase=PHASE_DEVELOPMENT,
        prompt_file=str(prompt_file),
    )

    stale_session_id = "8e9806b7-stale-id"

    def fake_invoke_agent(
        config: AgentConfig,
        pf: str,
        *,
        options: object = None,
    ) -> list[str]:
        del config, pf, options
        raise AgentInvocationError(
            "claude",
            1,
            f"No conversation found with session ID: {stale_session_id}",
        )

    config = _make_config()
    state = _make_state(last_session_id=stale_session_id, session_preserve=True)
    result = runner_module._execute_agent_effect(
        effect,
        config,
        runner_module._AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(cmd="claude", output_flag="--json-stream")
            ),
        ),
        WorkspaceScope(tmp_path),
        state=state,
    )

    assert result == PipelineEvent.AGENT_FAILURE


def test_runner_stale_session_full_recovery_cycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Full recovery cycle: stale-session AGENT_FAILURE → controller clears session
    and writes hint → second _execute_agent_effect call uses no session_id and succeeds.

    This simulates the outer state-machine loop that the pipeline runner performs:
    1. _execute_agent_effect fails with AGENT_FAILURE (stale session).
    2. RecoveryController.handle() clears last_agent_session_id, writes hint file.
    3. A retry prompt file is created containing the hint content.
    4. _execute_agent_effect is called again with new state (no session) and the retry prompt.
    5. The second invocation succeeds; we assert no session_id was passed.
    """
    monkeypatch.setattr(runner_module, "start_mcp_server", lambda *a, **kw: FakeBridge())
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _: None)
    monkeypatch.setattr(
        runner_module,
        "materialize_system_prompt",
        lambda *, workspace_root, name: str(tmp_path / "SYS.md"),
    )

    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    stale_session_id = "deadbeef-dead-dead-dead-deadbeefcafe"

    # --- Attempt 1: fail with stale session ---
    effect_1 = InvokeAgentEffect(
        agent_name="claude",
        phase=PHASE_DEVELOPMENT,
        prompt_file=str(prompt_file),
    )

    def fake_invoke_agent_first(
        config: AgentConfig,
        pf: str,
        *,
        options: object = None,
    ) -> list[str]:
        del config, pf, options
        raise AgentInvocationError(
            "claude",
            1,
            f"No conversation found with session ID: {stale_session_id}",
        )

    config = _make_config()
    state = _make_state(last_session_id=stale_session_id, session_preserve=True)
    result_1 = runner_module._execute_agent_effect(
        effect_1,
        config,
        runner_module._AgentExecutionDeps(
            invoke_agent=fake_invoke_agent_first,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(cmd="claude", output_flag="--json-stream")
            ),
        ),
        WorkspaceScope(tmp_path),
        state=state,
    )

    assert result_1 == PipelineEvent.AGENT_FAILURE

    # --- State machine: RecoveryController handles the failure ---
    exc = AgentInvocationError(
        "claude",
        1,
        f"No conversation found with session ID: {stale_session_id}",
    )
    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    new_state, _effects, evt = controller.handle(
        state, exc, phase=PHASE_DEVELOPMENT, agent="claude"
    )

    # Controller cleared the session id
    assert new_state.last_agent_session_id is None
    assert new_state.session_preserve_retry_pending is False

    # Budget debited
    assert evt.counted_against_budget is True

    # Hint file written
    hint_file = tmp_path / ".agent" / "tmp" / f"last_retry_error_{PHASE_DEVELOPMENT}.txt"
    assert hint_file.exists(), "RecoveryController must write a hint file for stale-session"
    hint_content = hint_file.read_text(encoding="utf-8")
    assert "session" in hint_content.lower()

    # --- Simulate prompt re-materialization: embed hint into retry prompt ---
    retry_prompt_file = tmp_path / "RETRY_PROMPT.md"
    retry_prompt_file.write_text(
        prompt_file.read_text(encoding="utf-8") + "\n\n" + hint_content,
        encoding="utf-8",
    )

    # --- Attempt 2: should use no session_id and succeed ---
    seen_session_ids: list[str | None] = []

    def fake_invoke_agent_second(
        config: AgentConfig,
        pf: str,
        *,
        options: object = None,
    ) -> list[str]:
        del config, pf
        seen_session_ids.append(
            getattr(options, "session_id", None) if options is not None else None
        )
        return []

    effect_2 = InvokeAgentEffect(
        agent_name="claude",
        phase=PHASE_DEVELOPMENT,
        prompt_file=str(retry_prompt_file),
    )
    result_2 = runner_module._execute_agent_effect(
        effect_2,
        config,
        runner_module._AgentExecutionDeps(
            invoke_agent=fake_invoke_agent_second,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(cmd="claude", output_flag="--json-stream")
            ),
        ),
        WorkspaceScope(tmp_path),
        state=new_state,  # new state has no session id
    )

    assert result_2 == PipelineEvent.AGENT_SUCCESS

    # Second invocation must not resume a stale session
    assert seen_session_ids == [None], (
        f"Second attempt must start with no resume session_id, got {seen_session_ids}"
    )

    # Retry prompt contains the hint content
    assert hint_content in retry_prompt_file.read_text(encoding="utf-8")

    # Only one retry budget consumed
    budget = controller.budget_registry.get(PHASE_DEVELOPMENT, "claude")
    assert budget is not None
    assert budget.consumed == 1


# ---------------------------------------------------------------------------
# Controller-level tests (pure logic, no runner)
# ---------------------------------------------------------------------------


def test_stale_session_path_full_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full sequence: stale-session failure leads to session cleared and retry hint written."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    session_id = "8e9806b7-8bcd-467b-b36a-6b076641d836"
    exc = AgentInvocationError(
        "claude",
        1,
        f"No conversation found with session ID: {session_id}",
    )

    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(last_session_id=session_id, session_preserve=True)

    # Attempt 1: fails with stale session
    new_state, _, evt = controller.handle(
        state, exc, phase=PHASE_DEVELOPMENT, agent="claude"
    )

    # Budget is debited
    assert evt.counted_against_budget is True
    assert evt.category == "agent"

    # Session state is cleared
    assert new_state.last_agent_session_id is None
    assert new_state.session_preserve_retry_pending is False

    # No exit effect — retry allowed
    assert new_state.phase == PHASE_DEVELOPMENT

    # Retry hint is on disk
    hint_file = tmp_path / ".agent" / "tmp" / f"last_retry_error_{PHASE_DEVELOPMENT}.txt"
    assert hint_file.exists()
    hint_content = hint_file.read_text(encoding="utf-8")
    assert "session" in hint_content.lower()

    # Only one retry consumed
    budget = controller.budget_registry.get(PHASE_DEVELOPMENT, "claude")
    assert budget is not None
    assert budget.consumed == 1


def test_stale_session_attempt2_uses_fresh_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After session reset, next attempt has no session ID to resume."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    session_id = "deadbeef-dead-dead-dead-deadbeefcafe"
    exc = AgentInvocationError(
        "claude",
        1,
        f"No conversation found with session ID: {session_id}",
    )

    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(last_session_id=session_id, session_preserve=True)

    new_state, _, _ = controller.handle(
        state, exc, phase=PHASE_DEVELOPMENT, agent="claude"
    )

    # Simulate how the runner computes resume_session_id from state
    resume_session_id = (
        new_state.last_agent_session_id
        if (
            new_state.session_preserve_retry_pending
            and new_state.last_agent_session_id
        )
        else None
    )
    assert resume_session_id is None, (
        "Second attempt must start with a fresh (None) session id, "
        f"got {resume_session_id!r}"
    )


def test_classifier_stale_session_only_on_exact_substring() -> None:
    """Only the exact session-not-found substring triggers reset_session=True."""
    classifier = FailureClassifier()

    stale_exc = AgentInvocationError(
        "claude",
        1,
        "No conversation found with session ID: abc123",
    )
    failure = classifier.classify(stale_exc, phase=PHASE_DEVELOPMENT, agent="claude")
    assert failure.reset_session is True
    assert failure.category == FailureCategory.AGENT

    generic_exc = AgentInvocationError("claude", 1, "agent exited unexpectedly")
    generic_failure = classifier.classify(generic_exc, phase=PHASE_DEVELOPMENT, agent="claude")
    assert generic_failure.reset_session is False


def test_stale_session_phase_remains_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale-session failure does not advance to PHASE_FAILED on first occurrence."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    exc = AgentInvocationError(
        "claude",
        1,
        "No conversation found with session ID: xyz",
    )
    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(last_session_id="xyz")

    new_state, effects, _ = controller.handle(
        state, exc, phase=PHASE_DEVELOPMENT, agent="claude"
    )

    assert new_state.phase == PHASE_DEVELOPMENT
    assert effects == []
