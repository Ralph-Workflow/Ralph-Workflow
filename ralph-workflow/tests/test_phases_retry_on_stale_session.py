"""End-to-end tests: stale session failure triggers session reset and context-preserving retry.

These tests operate at two levels:
- RecoveryController / FailureClassifier level (pure logic tests)
- runner._execute_agent_effect level (true E2E: stale session is detected and retried
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
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.runner import WorkspaceScope
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions

if TYPE_CHECKING:
    import pytest
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


def _registry_factory(agent_config: AgentConfig) -> type:
    """Return a registry factory class stub that always resolves to agent_config."""

    class _Registry:
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> _FakeRegistryInstance:
            del cls, config
            return _FakeRegistryInstance(agent_config)

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

    result = runner_module.execute_agent_effect(
        effect,
        config,
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(cmd="claude", output_flag="--json-stream")
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
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

    result = runner_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(
                    cmd="claude",
                    output_flag="--json-stream",
                    session_flag="--resume {}",
                )
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        state=_make_state(),
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

    result = runner_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(
                    cmd="claude",
                    output_flag="--json-stream",
                    session_flag="--resume {}",
                )
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        state=_make_state(),
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

    result = runner_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(
                    cmd="claude",
                    output_flag="--json-stream",
                    session_flag="--resume {}",
                )
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        state=_make_state(last_session_id="stale-original"),
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert len(captured_calls) == _EXPECTED_INVOCATION_COUNT
    assert captured_calls[1] is None


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
    result = runner_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        config,
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(cmd="claude", output_flag="--json-stream")
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        state=state,
    )

    assert result == PipelineEvent.AGENT_FAILURE


# ---------------------------------------------------------------------------
# OpenCode-specific stale-session runner tests
# ---------------------------------------------------------------------------


def test_runner_opencode_stale_session_internal_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OpenCode stale-session: _execute_agent_effect retries with fresh session after failure.

    OpenCode-specific variant using 'Session not found' stale-session message and
    OpenCode transport. Proves the runner stale-session detection and session-reset
    path works end-to-end for OpenCode agents, not only for Claude.

    Assertions:
    1. First invocation fails with OpenCode stale-session error ('Session not found').
    2. Second invocation (same _execute_agent_effect call) uses session_id=None.
    3. AGENT_SUCCESS is returned.
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

    result = runner_module.execute_agent_effect(
        effect,
        config,
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(
                    cmd="opencode",
                    output_flag="--format json",
                    session_flag="--session {}",
                    transport=AgentTransport.OPENCODE,
                )
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        state=state,
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

    result = runner_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(
                    cmd="opencode",
                    output_flag="--format json",
                    session_flag="--session {}",
                    transport=AgentTransport.OPENCODE,
                )
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        state=_make_state(last_session_id="deadbeef"),
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

    result = runner_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        ),
        _make_config(),
        runner_module.AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            agent_registry=_registry_factory(
                AgentConfig(
                    cmd="opencode",
                    output_flag="--format json",
                    session_flag="--session {}",
                    transport=AgentTransport.OPENCODE,
                )
            ),
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        state=_make_state(last_session_id="lower-case-deadbeef"),
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
