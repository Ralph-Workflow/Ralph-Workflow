"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    InvokeOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.config.general_config import GeneralConfig
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest
from tests._session_fake_mcp_bridge import _FakeMcpBridge
from tests._session_registry_factory import _RegistryFactory

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestRunnerSessionContinuation:
    """Runner correctly threads OpenCodeResumableExitError.session_id into the retry attempt."""

    def test_opencode_resumable_exit_retries_with_same_session_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """First call raises OpenCodeResumableExitError; second call gets session_id='sess-1'."""
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the task", encoding="utf-8")

        effect = InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = _opencode_agent_config()
        registry = _registry_factory_for(agent_config)
        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_a, **_kw: _FakeMcpBridge(),
        )

        seen_session_ids: list[str | None] = []

        def fake_invoke_agent(
            config: AgentConfig,
            prompt_file: str,
            *,
            options: InvokeOptions | None = None,
        ) -> Iterator[object]:
            del config, prompt_file
            seen_session_ids.append(options.session_id if options is not None else None)
            if len(seen_session_ids) == 1:

                def _first() -> Iterator[object]:
                    yield '{"type":"text"}'
                    raise OpenCodeResumableExitError("opencode", session_id="sess-1")

                return _first()
            return iter(['{"type":"result"}'])

        result = runner_module.execute_agent_effect(
            effect,
            _runner_config(max_retries=1),
            runner_module.AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert seen_session_ids == [None, "sess-1"], (
            f"Expected [None, 'sess-1'], got {seen_session_ids}"
        )

    def test_opencode_resumable_exit_no_more_attempts_returns_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With max_retries=0, OpenCodeResumableExitError is not retried and returns FAILURE."""
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the task", encoding="utf-8")

        effect = InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = _opencode_agent_config()
        registry = _registry_factory_for(agent_config)
        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_a, **_kw: _FakeMcpBridge(),
        )

        def fake_invoke_agent(
            config: AgentConfig,
            prompt_file: str,
            *,
            options: InvokeOptions | None = None,
        ) -> Iterator[object]:
            del config, prompt_file, options

            def _first() -> Iterator[object]:
                yield '{"type":"text"}'
                raise OpenCodeResumableExitError("opencode", session_id="sess-1")

            return _first()

        result = runner_module.execute_agent_effect(
            effect,
            _runner_config(max_retries=0),
            runner_module.AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_FAILURE


def _opencode_agent_config() -> AgentConfig:
    return AgentConfig(cmd="opencode", output_flag="--json-stream")


def _registry_factory_for(
    agent_config: AgentConfig,
) -> type[_RegistryFactory]:
    _RegistryFactory._agent_config = agent_config
    return _RegistryFactory


def _runner_config(max_retries: int = 1) -> UnifiedConfig:
    return UnifiedConfig(general=GeneralConfig(max_retries=max_retries))
