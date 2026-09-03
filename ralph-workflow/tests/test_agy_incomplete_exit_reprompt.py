"""Black-box regression tests for AGY incomplete-exit bounded reprompt behavior.

Contract under test (see
``ralph/agents/invoke/_agy_incomplete_exit_error.py``):

  * An AGY run that exits rc=0 WITHOUT the required completion evidence
    gets exactly ONE automatic recovery reprompt: a fresh invocation
    carrying the original task plus an explicit completion instruction.
  * The reprompt is spent once per invocation — a second incomplete exit
    is terminal (``AGENT_FAILURE``), never an unbounded retry loop.
  * When same-agent retries are disabled, no reprompt happens.
  * A reprompt that leaves the required evidence succeeds; the reprompt
    itself is never treated as completion evidence.
"""

from __future__ import annotations

import types
from pathlib import Path
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


def _agy_check_options(tmp_path: Path) -> CompletionCheckOptions:
    return CompletionCheckOptions(
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
        agy_cli_log_path=tmp_path / "cli.log",
    )


def _write_completion_sentinel(tmp_path: Path) -> None:
    sentinel = tmp_path / ".agent" / "completion_seen_agy.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "agy"}', encoding="utf-8")


def _run_effect(
    tmp_path: Path,
    fake_invoke_agent: object,
    config: UnifiedConfig,
) -> PipelineEvent:
    ctx = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        display_context=ctx,
        bridge=_FakeMcpBridge(),
        registry_factory=_RegistryFactory.from_config,
    )
    prompt_file = tmp_path / "PROMPT.md"
    effect = InvokeAgentEffect(agent_name="agy", phase="development", prompt_file=str(prompt_file))
    return effect_executor_module.execute_agent_effect(
        effect,
        config,
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=ctx,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )


def _make_missing_completion_fake(
    tmp_path: Path,
    invoke_count: list[int],
    prompt_files: list[str],
) -> object:
    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterator[str]:
        del config, options
        invoke_count[0] += 1
        prompt_files.append(prompt_file)

        def _gen() -> Iterator[str]:
            yield "output line"
            fake_handle = types.SimpleNamespace(returncode=0)
            check_process_result(fake_handle, "agy", [], _agy_check_options(tmp_path))

        return _gen()

    return fake_invoke_agent


def _setup(tmp_path: Path) -> None:
    (tmp_path / "PROMPT.md").write_text("implement the task", encoding="utf-8")
    _RegistryFactory._agent_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)


def test_agy_missing_completion_reprompts_exactly_once_then_fails(tmp_path: Path) -> None:
    """One bounded reprompt: 2 invocations total, then terminal AGENT_FAILURE."""
    _setup(tmp_path)
    invoke_count = [0]
    prompt_files: list[str] = []

    result = _run_effect(
        tmp_path,
        _make_missing_completion_fake(tmp_path, invoke_count, prompt_files),
        UnifiedConfig(general=GeneralConfig(max_same_agent_retries=2)),
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert invoke_count[0] == 2, (
        f"AGY missing-completion must reprompt exactly once; invoke_count={invoke_count[0]}"
    )


def test_agy_reprompt_prompt_carries_completion_instruction_and_original_task(
    tmp_path: Path,
) -> None:
    """The reprompt is a fresh invocation: original task plus explicit completion instruction."""
    _setup(tmp_path)
    invoke_count = [0]
    prompt_files: list[str] = []

    result = _run_effect(
        tmp_path,
        _make_missing_completion_fake(tmp_path, invoke_count, prompt_files),
        UnifiedConfig(general=GeneralConfig(max_same_agent_retries=2)),
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert len(prompt_files) == 2
    text = Path(prompt_files[1]).read_text(encoding="utf-8")
    assert "COMPLETION RECOVERY INSTRUCTION" in text
    assert "declare_complete" in text
    assert "ralph_submit_md_artifact" in text
    assert "ORIGINAL TASK PROMPT:" in text
    assert "implement the task" in text


def test_agy_missing_completion_does_not_reprompt_when_retries_disabled(
    tmp_path: Path,
) -> None:
    """With a zero same-agent retry budget the incomplete exit is immediately terminal."""
    _setup(tmp_path)
    invoke_count = [0]
    prompt_files: list[str] = []

    result = _run_effect(
        tmp_path,
        _make_missing_completion_fake(tmp_path, invoke_count, prompt_files),
        UnifiedConfig(general=GeneralConfig(max_same_agent_retries=0)),
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert invoke_count[0] == 1


def test_agy_reprompt_with_completion_evidence_succeeds(tmp_path: Path) -> None:
    """A reprompt that leaves the sentinel succeeds — the evidence is re-earned, never assumed."""
    _setup(tmp_path)
    invoke_count = [0]
    prompt_files: list[str] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterator[str]:
        del config, options
        invoke_count[0] += 1
        prompt_files.append(prompt_file)

        def _gen() -> Iterator[str]:
            declare_line = "Task declared complete: session_id=agy, summary=done, timestamp=1"
            output: list[str] = []
            if invoke_count[0] == 2:
                # The reprompt earns the completion evidence for real.
                _write_completion_sentinel(tmp_path)
                output.append(declare_line)
            yield "output line"
            fake_handle = types.SimpleNamespace(returncode=0)
            check_process_result(fake_handle, "agy", output, _agy_check_options(tmp_path))

        return _gen()

    result = _run_effect(
        tmp_path,
        fake_invoke_agent,
        UnifiedConfig(general=GeneralConfig(max_same_agent_retries=2)),
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert invoke_count[0] == 2


def test_agy_completion_evidenced_run_does_not_fail(tmp_path: Path) -> None:
    """A first attempt that leaves the evidence never triggers the reprompt."""
    _setup(tmp_path)
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
        _write_completion_sentinel(tmp_path)

        def _gen() -> Iterator[str]:
            yield declare_line
            fake_handle = types.SimpleNamespace(returncode=0)
            check_process_result(
                fake_handle,
                "agy",
                [declare_line],
                _agy_check_options(tmp_path),
            )

        return _gen()

    result = _run_effect(
        tmp_path,
        fake_invoke_agent,
        UnifiedConfig(general=GeneralConfig(max_same_agent_retries=0)),
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert invoke_count[0] == 1
