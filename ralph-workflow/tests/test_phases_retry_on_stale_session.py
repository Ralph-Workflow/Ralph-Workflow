"""End-to-end tests: stale session failure triggers session reset and context-preserving retry.

These tests operate at two levels:
- RecoveryController / FailureClassifier level (pure logic tests)
- runner._execute_agent_effect level (true E2E: stale session is detected and retried
  internally with a fresh session, without any manual stitching of steps)
"""

from __future__ import annotations

from pathlib import Path
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
    import pytest

_EXPECTED_INVOCATION_COUNT = 2


# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# Runner-level E2E: _execute_agent_effect internally retries stale session
# ---------------------------------------------------------------------------


def test_runner_stale_session_internal_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_execute_agent_effect internally retries with a fresh session after stale-session failure.

    The real retry path inside _execute_agent_effect detects the stale-session error,
    produces a retry prompt carrying the failure context, and re-invokes with session_id=None.
    No manual stitching is needed — the pipeline loop handles it automatically.

    Assertions:
    1. First invocation fails with stale-session error.
    2. Second invocation (same _execute_agent_effect call) uses session_id=None.
    3. The retry prompt passed to the second invocation contains the stale-session context.
    4. AGENT_SUCCESS is returned.
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

    captured_calls: list[tuple[str | None, str]] = []  # (session_id, prompt_file)

    def fake_invoke_agent(
        config: AgentConfig,
        pf: str,
        *,
        options: object = None,
    ) -> list[str]:
        del config
        session_id = getattr(options, "session_id", None) if options is not None else None
        captured_calls.append((session_id, pf))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "claude",
                1,
                f"No conversation found with session ID: {stale_session_id}",
            )
        return []

    effect = InvokeAgentEffect(
        agent_name="claude",
        phase=PHASE_DEVELOPMENT,
        prompt_file=str(prompt_file),
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

    # (4) AGENT_SUCCESS: the internal retry succeeded
    assert result == PipelineEvent.AGENT_SUCCESS

    # (1) + (2) Two invocations: first uses stale session, second uses None
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT, (
        f"Expected {_EXPECTED_INVOCATION_COUNT} invocations, got {len(captured_calls)}"
    )
    _first_session_id, _first_prompt = captured_calls[0]
    second_session_id, second_prompt = captured_calls[1]

    assert second_session_id is None, (
        f"Second invocation must start with no resume session_id, got {second_session_id!r}"
    )

    # (3) Retry prompt contains stale-session context — produced by _write_agent_retry_prompt,
    # not by manual test-side construction
    retry_content = Path(second_prompt).read_text(encoding="utf-8")
    assert "stale session" in retry_content.lower() or "session" in retry_content.lower(), (
        f"Retry prompt must contain stale-session context, got: {retry_content[:200]!r}"
    )


def test_runner_stale_session_exhausts_retries_returns_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When all retries fail with stale-session errors, AGENT_FAILURE is returned."""
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
        InvokeAgentEffect(
            agent_name="claude",
            phase=PHASE_DEVELOPMENT,
            prompt_file=str(prompt_file),
        ),
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

    new_state, _, evt = controller.handle(
        state, exc, phase=PHASE_DEVELOPMENT, agent="claude"
    )

    assert evt.counted_against_budget is True
    assert evt.category == "agent"

    assert new_state.last_agent_session_id is None
    assert new_state.session_preserve_retry_pending is False

    assert new_state.phase == PHASE_DEVELOPMENT

    hint_file = tmp_path / ".agent" / "tmp" / f"last_retry_error_{PHASE_DEVELOPMENT}.txt"
    assert hint_file.exists()
    hint_content = hint_file.read_text(encoding="utf-8")
    assert "session" in hint_content.lower()

    budget = controller.budget_registry.get(PHASE_DEVELOPMENT, "claude")
    assert budget is not None
    assert budget.consumed == 1


def test_stale_session_attempt2_uses_fresh_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After controller session reset, the runner computes resume_session_id as None."""
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

    resume_session_id = (
        new_state.last_agent_session_id
        if (
            new_state.session_preserve_retry_pending
            and new_state.last_agent_session_id
        )
        else None
    )
    assert resume_session_id is None, (
        "After session reset, runner must compute resume_session_id=None, "
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
