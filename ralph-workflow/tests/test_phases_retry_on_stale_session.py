"""End-to-end tests: stale session failure triggers session reset and context-preserving retry.

These tests operate at two levels:
- RecoveryController / FailureClassifier level (pure logic tests)
- effect_executor.execute_agent_effect level (true E2E: stale session is detected and retried
  internally with a fresh session, without any manual stitching of steps)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InactivityTimeoutOpts,
    InvokeOptions,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.agent_recovery_input import AgentRecoveryInput
from ralph.pipeline.effect_executor import build_agent_recovery_plan
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

    from ralph.pipeline.factory import MaterializeSystemPromptFn

from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_phases_retry_on_stale_session_helper__fakeregistryinstance import (
    _FakeRegistryInstance,
)

_EXPECTED_INVOCATION_COUNT = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    last_session_id: str | None = None,
) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
        last_agent_session_id=last_session_id,
    )


def _make_config(
    agent_idle_timeout_seconds: float = 300.0,
    *,
    max_same_agent_retries: int | None = None,
) -> UnifiedConfig:
    general = GeneralConfig(agent_idle_timeout_seconds=agent_idle_timeout_seconds)
    if max_same_agent_retries is not None:
        general = general.model_copy(update={"max_same_agent_retries": max_same_agent_retries})
    return UnifiedConfig(general=general)


class FakeBridge:
    """Minimal MCP bridge stub for runner tests."""

    def shutdown(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:19999/mcp"

    def reset_tool_registry(self) -> None:
        return


def _system_prompt_materializer(tmp_path: Path) -> MaterializeSystemPromptFn:
    def _materialize(
        workspace_root: Path,
        name: str,
        default_current_prompt: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, name, default_current_prompt, worker_namespace
        return str(tmp_path / "SYS.md")

    return _materialize


def _registry_factory(agent_config: AgentConfig) -> type:
    """Return a registry factory class stub that always resolves to agent_config."""

    class _Registry:
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> _FakeRegistryInstance:
            del cls, config
            return _FakeRegistryInstance(agent_config)

    return _Registry


# ---------------------------------------------------------------------------
# Runner-level E2E: execute_agent_effect internally retries stale session
# ---------------------------------------------------------------------------


def test_runner_stale_session_internal_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """execute_agent_effect internally retries with a fresh session after stale-session failure.

    The real retry path inside execute_agent_effect detects the stale-session error,
    produces a retry prompt carrying the failure context, and re-invokes with session_id=None.
    No manual stitching is needed — the pipeline loop handles it automatically.

    Assertions:
    1. First invocation fails with stale-session error.
    2. Second invocation (same execute_agent_effect call) uses session_id=None.
    3. The retry prompt passed to the second invocation contains the stale-session context.
    4. AGENT_SUCCESS is returned.
    """
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    stale_session_id = "deadbeef-dead-dead-dead-deadbeefcafe"

    captured_calls: list[tuple[str | None, str]] = []  # (session_id, prompt_file)

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        session_id = options.session_id if options is not None else None
        captured_calls.append((session_id, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "claude",
                1,
                f"No conversation found with session ID: {stale_session_id}",
            )
        return []

    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file=str(prompt_file),
    )
    config = _make_config()
    state = _make_state(last_session_id=stale_session_id)
    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(cmd="claude", output_flag="--json-stream")
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        effect,
        config,
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=state,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
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
    assert retry_content.splitlines()[0] == "ERROR RECOVERY REQUIRED"
    assert (
        "stale session id" in retry_content.lower()
        or "fresh session required" in retry_content.lower()
    )
    assert "do not restart the task from scratch" in retry_content.lower()
    assert "original prompt:" in retry_content.lower()
    assert str(prompt_file) in retry_content
    assert "previous context summary:" in retry_content.lower()

    context_match = re.search(r"Previous context summary:\s*`([^`]+)`", retry_content)
    assert context_match is not None
    context_path = Path(context_match.group(1))
    assert context_path.exists()
    context_text = context_path.read_text(encoding="utf-8").lower()
    assert "no conversation found with session id" in context_text


def test_runner_inactivity_timeout_with_captured_session_retries_same_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Claude interactive inactivity retry preserves the observed session ID."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")
    captured_session_id = "unsafe-after-kill"
    captured_session = f'{{"type":"session","session_id":"{captured_session_id}"}}'
    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        session_id = options.session_id if options is not None else None
        captured_calls.append((session_id, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInactivityTimeoutError(
                "claude",
                300.0,
                [captured_session],
                InactivityTimeoutOpts(
                    reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
                    session_resume_safe=True,
                ),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="claude",
                output_flag="--json-stream",
                session_flag="--resume {}",
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    second_session_id, second_prompt = captured_calls[1]
    assert second_session_id == captured_session_id
    retry_content = Path(second_prompt).read_text(encoding="utf-8")
    assert retry_content.splitlines()[0] == "ERROR RECOVERY REQUIRED"
    assert "inactivity timeout" in retry_content.lower()
    assert "the exact cause may be unknown" in retry_content.lower()
    assert "original prompt:" in retry_content.lower()
    assert str(prompt_file) in retry_content


def test_runner_retry_prompt_condenses_visible_output_in_real_retry_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")
    huge_line = "claude result: " + ("x" * 1200)
    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        session_id = options.session_id if options is not None else None
        captured_calls.append((session_id, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInactivityTimeoutError(
                "claude",
                300.0,
                [huge_line] * 20,
                InactivityTimeoutOpts(
                    reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
                    session_resume_safe=True,
                ),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="claude",
                output_flag="--json-stream",
                session_flag="--resume {}",
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    retry_content = Path(captured_calls[1][1]).read_text(encoding="utf-8")
    assert "<previous log omitted>" in retry_content
    assert huge_line not in retry_content
    assert " ... (truncated)" in retry_content


def test_runner_stale_session_with_parsed_session_id_retries_fresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stale-session errors force a fresh retry even when output has another session ID."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")
    captured_calls: list[str | None] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config, prompt_file
        captured_calls.append(options.session_id if options is not None else None)
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "claude",
                1,
                "No conversation found with session ID: stale-original",
                parsed_output=['{"type":"session","session_id":"different-session-from-output"}'],
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="claude",
                output_flag="--json-stream",
                session_flag="--resume {}",
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id="stale-original"),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    assert captured_calls[1] is None


def test_runner_stale_session_exhausts_retries_returns_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When all retries fail with stale-session errors, AGENT_FAILURE is returned."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    stale_session_id = "8e9806b7-stale-id"

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config, prompt_file, options
        raise AgentInvocationError(
            "claude",
            1,
            f"No conversation found with session ID: {stale_session_id}",
        )

    config = _make_config()
    state = _make_state(last_session_id=stale_session_id)
    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(cmd="claude", output_flag="--json-stream")
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        config,
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=state,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_FAILURE


# ---------------------------------------------------------------------------
# OpenCode-specific stale-session runner tests
# ---------------------------------------------------------------------------


def test_runner_opencode_stale_session_internal_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OpenCode stale-session: execute_agent_effect retries with fresh session after failure.

    OpenCode-specific variant using 'Session not found' stale-session message and
    OpenCode transport. Proves the runner stale-session detection and session-reset
    path works end-to-end for OpenCode agents, not only for Claude.

    Assertions:
    1. First invocation fails with OpenCode stale-session error ('Session not found').
    2. Second invocation (same execute_agent_effect call) uses session_id=None.
    3. AGENT_SUCCESS is returned.
    """
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    opencode_stale_session_id = "opencode-stale-abc123"

    captured_calls: list[tuple[str | None, str]] = []  # (session_id, prompt_file)

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        session_id = options.session_id if options is not None else None
        captured_calls.append((session_id, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "opencode",
                1,
                f"Session not found: {opencode_stale_session_id}",
            )
        return []

    effect = InvokeAgentEffect(
        agent_name="opencode",
        phase="development",
        prompt_file=str(prompt_file),
    )
    config = _make_config()
    state = _make_state(last_session_id=opencode_stale_session_id)
    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="opencode",
                output_flag="--format json",
                session_flag="--session {}",
                transport=AgentTransport.OPENCODE,
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        effect,
        config,
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=state,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    # (3) AGENT_SUCCESS: the internal retry with fresh session succeeded
    assert result == PipelineEvent.AGENT_SUCCESS

    # (1) + (2) Two invocations: first uses stale session, second uses None
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT, (
        f"Expected {_EXPECTED_INVOCATION_COUNT} invocations, got {len(captured_calls)}"
    )
    second_session_id, _second_prompt = captured_calls[1]
    assert second_session_id is None, (
        f"Second invocation must use no resume session_id after stale session reset; "
        f"got {second_session_id!r}"
    )


def test_runner_opencode_unknown_session_stale_message_triggers_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OpenCode 'Unknown session' message also triggers stale-session retry with fresh session."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    captured_calls: list[str | None] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config, prompt_file
        session_id = options.session_id if options is not None else None
        captured_calls.append(session_id)
        if len(captured_calls) == 1:
            raise AgentInvocationError("opencode", 1, "Unknown session: deadbeef")
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="opencode",
                output_flag="--format json",
                session_flag="--session {}",
                transport=AgentTransport.OPENCODE,
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id="deadbeef"),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    assert captured_calls[1] is None, (
        f"Second call must use session_id=None after Unknown session reset; "
        f"got {captured_calls[1]!r}"
    )


def test_runner_opencode_lowercase_stale_message_in_parsed_output_triggers_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Lowercase stale-session details in parsed_output still force a fresh retry session."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    captured_calls: list[str | None] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config, prompt_file
        captured_calls.append(options.session_id if options is not None else None)
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "opencode",
                1,
                "Unexpected server error",
                ['{"type":"error","error":{"message":"session not found: lower-case-deadbeef"}}'],
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="opencode",
                output_flag="--format json",
                session_flag="--session {}",
                transport=AgentTransport.OPENCODE,
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id="lower-case-deadbeef"),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    assert captured_calls[1] is None


# ---------------------------------------------------------------------------
# Controller-level tests (pure logic, no runner)
# ---------------------------------------------------------------------------


def test_stale_session_path_full_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Full sequence: stale-session failure leads to session cleared and retry hint written."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    session_id = "8e9806b7-8bcd-467b-b36a-6b076641d836"
    exc = AgentInvocationError(
        "claude",
        1,
        f"No conversation found with session ID: {session_id}",
    )

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(last_session_id=session_id)

    new_state, _, evt = controller.handle(
        state, exc, FailureContext(phase="development", agent="claude")
    )

    assert evt.counted_against_budget is True
    assert evt.category == "agent"

    assert new_state.last_agent_session_id is None
    assert new_state.agent_retry_intent.action is None

    assert new_state.phase == "development"

    hint_file = tmp_path / ".agent" / "tmp" / f"last_retry_error_{'development'}.txt"
    assert hint_file.exists()
    hint_content = hint_file.read_text(encoding="utf-8")
    assert "session" in hint_content.lower()

    budget = controller.budget_registry.get("development", "claude")
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

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(last_session_id=session_id)

    new_state, _, _ = controller.handle(
        state, exc, FailureContext(phase="development", agent="claude")
    )

    resume_session_id = new_state.agent_retry_intent.session_id
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
    failure = classifier.classify(stale_exc, phase="development", agent="claude")
    assert failure.reset_session is True
    assert failure.category == FailureCategory.AGENT

    generic_exc = AgentInvocationError("claude", 1, "agent exited unexpectedly")
    generic_failure = classifier.classify(generic_exc, phase="development", agent="claude")
    assert generic_failure.reset_session is False


def test_default_same_agent_retry_cap_is_visible_and_global() -> None:
    assert GeneralConfig().max_same_agent_retries == 10
    assert _make_config().general.max_same_agent_retries == 10


def test_stale_session_phase_remains_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale-session failure does not advance to "failed" on first occurrence."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    exc = AgentInvocationError(
        "claude",
        1,
        "No conversation found with session ID: xyz",
    )
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(last_session_id="xyz")

    new_state, effects, _ = controller.handle(
        state, exc, FailureContext(phase="development", agent="claude")
    )

    assert new_state.phase == "development"
    assert effects == []


# ---------------------------------------------------------------------------
# Stale-session retry prompt: full prior-output (untruncated) regression suite
#
# These tests pin that when the failure surfaces a stale-session marker
# (SESSION_NOT_FOUND_SUBSTRINGS), the retry prompt and its companion
# ``agent_retry_context_<uuid>.md`` file both contain the FULL captured
# prior output (no <previous log omitted> marker, no 12-line tail cap).
#
# Tests 1-5 are RED on the current code (12-line cap truncates regardless
# of failure type) and GREEN after the untruncated flag is wired.
# Tests 6-8 are GREEN on the current code (scope-confirmation: 12-line
# cap is preserved for resume / non-stale-fresh / inactivity-not-resume-safe).
# ---------------------------------------------------------------------------


def _extract_context_path(retry_content: str) -> Path:
    """Extract the ``agent_retry_context_<uuid>.md`` path from a retry prompt."""
    context_match = re.search(r"Previous context summary:\s*`([^`]+)`", retry_content)
    assert context_match is not None, (
        f"retry prompt is missing 'Previous context summary:' path line:\n{retry_content}"
    )
    return Path(context_match.group(1))


def test_stale_session_retry_prompt_includes_full_prior_output_marker_in_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OpenCode stale-session marker in stderr: full 30-line context preserved in retry prompt."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    prior_lines = [f"prior-output-line-{idx:03d}" for idx in range(30)]
    stale_session_id = "opencode-stale-stderr-marker"

    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        captured_calls.append((options.session_id if options is not None else None, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "opencode",
                1,
                stderr=f"Error: Session not found for ID: {stale_session_id}",
                parsed_output=list(prior_lines),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="opencode",
                output_flag="--format json",
                session_flag="--session {}",
                transport=AgentTransport.OPENCODE,
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id=stale_session_id),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    _second_session_id, second_prompt = captured_calls[1]
    retry_content = Path(second_prompt).read_text(encoding="utf-8")

    # No omission marker: the FULL captured prior output must be preserved.
    assert "<previous log omitted>" not in retry_content, (
        "stale-session retry must NOT inject <previous log omitted> marker"
    )

    # Every line of the 30-line prior output must appear in the retry prompt.
    for line in prior_lines:
        assert line in retry_content, (
            f"stale-session retry prompt is missing prior output line {line!r}"
        )

    # Same invariant must hold for the companion context file.
    context_path = _extract_context_path(retry_content)
    assert context_path.exists()
    context_text = context_path.read_text(encoding="utf-8")
    assert "<previous log omitted>" not in context_text, (
        "stale-session agent_retry_context file must NOT inject <previous log omitted> marker"
    )
    for line in prior_lines:
        assert line in context_text, (
            f"stale-session agent_retry_context file is missing prior output line {line!r}"
        )


def test_stale_session_retry_prompt_includes_full_prior_output_marker_in_str_exc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Claude 'No conversation found' marker in str(exc): full 30-line context preserved."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    prior_lines = [f"claude-stale-line-{idx:03d}" for idx in range(30)]
    stale_session_id = "claude-stale-str-exc-marker"

    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        captured_calls.append((options.session_id if options is not None else None, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "claude",
                1,
                stderr=f"No conversation found with session ID: {stale_session_id}",
                parsed_output=list(prior_lines),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="claude",
                output_flag="--json-stream",
                session_flag="--resume {}",
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id=stale_session_id),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    _second_session_id, second_prompt = captured_calls[1]
    retry_content = Path(second_prompt).read_text(encoding="utf-8")

    assert "<previous log omitted>" not in retry_content
    for line in prior_lines:
        assert line in retry_content, (
            f"claude stale-session retry prompt is missing prior output line {line!r}"
        )

    context_path = _extract_context_path(retry_content)
    context_text = context_path.read_text(encoding="utf-8")
    assert "<previous log omitted>" not in context_text
    for line in prior_lines:
        assert line in context_text


def test_stale_session_retry_prompt_includes_full_prior_output_marker_in_parsed_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stale-session marker surfaced via parsed_output: full 25-line context preserved."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    prior_lines = [f"parsed-output-line-{idx:03d}" for idx in range(24)]
    marker_item = '{"type":"error","message":"Session not found: parsed-output-stale-marker"}'
    parsed_output_items = [*prior_lines, marker_item]

    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        captured_calls.append((options.session_id if options is not None else None, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "opencode",
                1,
                stderr="agent exited",
                parsed_output=parsed_output_items,
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="opencode",
                output_flag="--format json",
                session_flag="--session {}",
                transport=AgentTransport.OPENCODE,
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id="parsed-output-stale-marker"),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    _second_session_id, second_prompt = captured_calls[1]
    retry_content = Path(second_prompt).read_text(encoding="utf-8")

    assert "<previous log omitted>" not in retry_content
    for line in prior_lines:
        assert line in retry_content
    assert "Session not found: parsed-output-stale-marker" in retry_content

    context_path = _extract_context_path(retry_content)
    context_text = context_path.read_text(encoding="utf-8")
    assert "<previous log omitted>" not in context_text
    for line in prior_lines:
        assert line in context_text
    assert "Session not found: parsed-output-stale-marker" in context_text


def test_stale_session_retry_with_rendered_output_includes_full_rendered_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stale-session with rendered_output fast-path: full 20 rendered lines preserved.

    The fast-path in ``_recovery_context_lines`` prefers rendered_output over
    parsed_output; this test pins that the untruncated branch still applies
    when rendered_output is the chosen source.
    """
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    rendered_lines = [f"rendered-line-{idx:03d}" for idx in range(20)]
    stale_session_id = "rendered-output-stale-id"

    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file=str(prompt_file),
    )
    exc = AgentInvocationError(
        "claude",
        1,
        stderr=f"No conversation found with session ID: {stale_session_id}",
    )
    plan = build_agent_recovery_plan(
        AgentRecoveryInput(
            exc=exc,
            attempt_index=0,
            max_recovery_attempts=3,
            effect=effect,
            workspace_root=tmp_path,
            raw_output=[],
            rendered_output=rendered_lines,
            extracted_session_id=None,
            inactivity_error_type=AgentInactivityTimeoutError,
        )
    )
    assert plan is not None
    retry_content = Path(plan.prompt_file).read_text(encoding="utf-8")

    assert "<previous log omitted>" not in retry_content, (
        "stale-session rendered_output retry must NOT inject <previous log omitted> marker"
    )
    for line in rendered_lines:
        assert line in retry_content, (
            f"stale-session rendered_output retry prompt is missing line {line!r}"
        )

    context_path = _extract_context_path(retry_content)
    context_text = context_path.read_text(encoding="utf-8")
    assert "<previous log omitted>" not in context_text
    for line in rendered_lines:
        assert line in context_text


def test_stale_session_retry_preserves_per_line_character_truncation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Per-line 240-char cap still applies on stale-session retry, but line count is preserved."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")

    huge_line = "stale-result: " + ("x" * 1200)
    prior_lines = [huge_line for _ in range(20)]
    stale_session_id = "per-line-trunc-stale-id"

    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        captured_calls.append((options.session_id if options is not None else None, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "claude",
                1,
                stderr=f"Session not found: {stale_session_id}",
                parsed_output=list(prior_lines),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="claude",
                output_flag="--json-stream",
                session_flag="--resume {}",
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id=stale_session_id),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    _second_session_id, second_prompt = captured_calls[1]
    retry_content = Path(second_prompt).read_text(encoding="utf-8")

    assert "<previous log omitted>" not in retry_content, (
        "stale-session retry must NOT inject <previous log omitted> marker even for huge lines"
    )
    assert huge_line not in retry_content, (
        "per-line 240-char cap must still apply: full 1200-char line must be truncated"
    )
    assert " ... (truncated)" in retry_content, (
        "per-line 240-char cap must still apply: '... (truncated)' suffix must be present"
    )

    context_path = _extract_context_path(retry_content)
    context_text = context_path.read_text(encoding="utf-8")
    assert "<previous log omitted>" not in context_text
    assert huge_line not in context_text
    assert " ... (truncated)" in context_text


def test_resume_retry_still_uses_12_line_tail_for_non_stale_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Scope confirmation: resume path keeps the 12-line tail cap (no untruncated widening)."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")
    huge_line = "claude result: " + ("x" * 1200)
    prior_lines = [huge_line for _ in range(20)]
    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        session_id = options.session_id if options is not None else None
        captured_calls.append((session_id, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInactivityTimeoutError(
                "claude",
                300.0,
                list(prior_lines),
                InactivityTimeoutOpts(
                    reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
                    session_resume_safe=True,
                ),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="claude",
                output_flag="--json-stream",
                session_flag="--resume {}",
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    _second_session_id, second_prompt = captured_calls[1]
    retry_content = Path(second_prompt).read_text(encoding="utf-8")

    assert "<previous log omitted>" in retry_content, (
        "resume path must still use the 12-line tail cap; <previous log omitted> is required"
    )
    assert huge_line not in retry_content
    assert " ... (truncated)" in retry_content


def test_non_stale_fresh_retry_still_uses_12_line_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Scope confirmation: transient-connectivity fresh retry keeps the 12-line cap."""
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")
    prior_lines = [f"transient-line-{idx:03d}" for idx in range(25)]
    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        captured_calls.append((options.session_id if options is not None else None, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInvocationError(
                "opencode",
                1,
                stderr="client offline",
                parsed_output=list(prior_lines),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="opencode",
                output_flag="--format json",
                session_flag="--session {}",
                transport=AgentTransport.OPENCODE,
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(last_session_id="prior-transient-session"),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    _second_session_id, second_prompt = captured_calls[1]
    retry_content = Path(second_prompt).read_text(encoding="utf-8")

    assert "<previous log omitted>" in retry_content, (
        "non-stale fresh retry must still use the 12-line tail cap"
    )
    assert "transient-line-000" not in retry_content, (
        "non-stale fresh retry must drop lines beyond the 12-line tail cap"
    )
    assert "transient-line-013" in retry_content, (
        "non-stale fresh retry must keep the last 12 lines"
    )
    assert "transient-line-024" in retry_content, (
        "non-stale fresh retry must keep the last 12 lines (last entry in tail)"
    )

    context_path = _extract_context_path(retry_content)
    context_text = context_path.read_text(encoding="utf-8")
    assert "<previous log omitted>" in context_text


def test_inactivity_timeout_with_session_resume_safe_false_still_uses_12_line_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Scope confirmation: inactivity timeout with session_resume_safe=False keeps the 12-line cap.

    ``_failure_requires_fresh_session`` returns True for this case (fresh session)
    but ``_is_stale_session_failure`` (the new helper) returns False because no
    SESSION_NOT_FOUND_SUBSTRINGS marker is present. The 12-line cap MUST still
    apply -- the untruncated branch is scoped to stale-session markers ONLY per
    the user prompt.
    """
    (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the change", encoding="utf-8")
    huge_line = "claude result: " + ("x" * 1200)
    prior_lines = [huge_line for _ in range(20)]
    captured_calls: list[tuple[str | None, str]] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> list[str]:
        del config
        session_id = options.session_id if options is not None else None
        captured_calls.append((session_id, prompt_file))
        if len(captured_calls) == 1:
            raise AgentInactivityTimeoutError(
                "claude",
                300.0,
                list(prior_lines),
                InactivityTimeoutOpts(
                    reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
                    session_resume_safe=False,
                ),
            )
        return []

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        ctx,
        bridge=FakeBridge(),
        registry_factory=_registry_factory(
            AgentConfig(
                cmd="claude",
                output_flag="--json-stream",
                session_flag="--resume {}",
            )
        ).from_config,
        system_prompt_materializer=_system_prompt_materializer(tmp_path),
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        state=_make_state(),
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    _second_session_id, second_prompt = captured_calls[1]
    retry_content = Path(second_prompt).read_text(encoding="utf-8")

    assert "<previous log omitted>" in retry_content, (
        "inactivity timeout with session_resume_safe=False must still use the 12-line tail cap; "
        "the untruncated branch is scoped to stale-session markers ONLY"
    )
    assert huge_line not in retry_content
    assert " ... (truncated)" in retry_content
