"""Black-box regression tests for AGY runner completion behavior."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.config.enums import AgentTransport
from ralph.config.general_config import GeneralConfig
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._session_fake_mcp_bridge import _FakeMcpBridge
from tests._session_registry_factory import _RegistryFactory

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def test_agy_missing_completion_does_not_retry(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")
    effect = InvokeAgentEffect(agent_name="agy", phase="development", prompt_file=str(prompt_file))
    _RegistryFactory._agent_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    registry = _RegistryFactory
    invoke_count = [0]

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterator[str]:
        del config, prompt_file, options
        invoke_count[0] += 1

        def _gen() -> Iterator[str]:
            yield "output line"
            fake_handle = types.SimpleNamespace(returncode=0)
            check_process_result(
                fake_handle,
                "agy",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy_for_transport(AgentTransport.AGY),
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                    ),
                    completion_run_id="agy",
                    # Isolate from the real, shared, host-global
                    # ~/.gemini/antigravity-cli/cli.log: agy_empty_output_reason's
                    # fallback reads that ambient file when no path is injected and
                    # the (here, empty) bounded output gives it no other evidence.
                    # A live AGY invocation elsewhere on this host can append a line
                    # matching one of the diagnostic patterns (e.g. the print-mode
                    # timeout symptom, which the CLI logs even for an otherwise
                    # unrelated process), changing the raised message and, with it,
                    # this test's retry-vs-no-retry outcome. Point at a nonexistent
                    # path scoped to tmp_path so this test's result depends only on
                    # what THIS test controls.
                    agy_cli_log_path=tmp_path / "cli.log",
                ),
            )

        return _gen()

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        display_context=ctx,
        bridge=_FakeMcpBridge(),
        registry_factory=registry.from_config,
    )
    result = effect_executor_module.execute_agent_effect(
        effect,
        UnifiedConfig(general=GeneralConfig(max_retries=0)),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert invoke_count[0] == 1


def test_agy_missing_completion_does_not_retry_even_when_retries_permitted(
    tmp_path: Path,
) -> None:
    """Plan S-7: the missing-completion failure is non-retryable by nature.

    Same shape as ``test_agy_missing_completion_does_not_retry`` but with
    ``max_retries=2``: the no-retry outcome must come from the failure's
    classification, not from retries merely being disabled in config.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")
    effect = InvokeAgentEffect(agent_name="agy", phase="development", prompt_file=str(prompt_file))
    _RegistryFactory._agent_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    registry = _RegistryFactory
    invoke_count = [0]

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterator[str]:
        del config, prompt_file, options
        invoke_count[0] += 1

        def _gen() -> Iterator[str]:
            yield "output line"
            fake_handle = types.SimpleNamespace(returncode=0)
            check_process_result(
                fake_handle,
                "agy",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy_for_transport(AgentTransport.AGY),
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                    ),
                    completion_run_id="agy",
                    agy_cli_log_path=tmp_path / "cli.log",
                ),
            )

        return _gen()

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        display_context=ctx,
        bridge=_FakeMcpBridge(),
        registry_factory=registry.from_config,
    )
    result = effect_executor_module.execute_agent_effect(
        effect,
        UnifiedConfig(general=GeneralConfig(max_retries=2)),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert invoke_count[0] == 1, (
        f"AGY missing-completion must not retry even with max_retries=2; "
        f"invoke_count={invoke_count[0]}"
    )


def test_agy_completion_evidenced_run_does_not_fail(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")
    effect = InvokeAgentEffect(agent_name="agy", phase="development", prompt_file=str(prompt_file))
    _RegistryFactory._agent_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    registry = _RegistryFactory
    invoke_count = [0]

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterator[str]:
        del config, prompt_file, options
        invoke_count[0] += 1
        declare_line = "Task declared complete: session_id=agy, summary=done, timestamp=1"
        sentinel = tmp_path / ".agent" / "completion_seen_agy.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text('{"run_id": "agy"}', encoding="utf-8")

        def _gen() -> Iterator[str]:
            yield declare_line
            fake_handle = types.SimpleNamespace(returncode=0)
            check_process_result(
                fake_handle,
                "agy",
                [declare_line],
                CompletionCheckOptions(
                    execution_strategy=strategy_for_transport(AgentTransport.AGY),
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                    ),
                    completion_run_id="agy",
                ),
            )

        return _gen()

    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        display_context=ctx,
        bridge=_FakeMcpBridge(),
        registry_factory=registry.from_config,
    )
    result = effect_executor_module.execute_agent_effect(
        effect,
        UnifiedConfig(general=GeneralConfig(max_retries=0)),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert invoke_count[0] == 1
