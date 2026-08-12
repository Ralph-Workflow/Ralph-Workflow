"""Dataclass types for agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.invoke._agent_run_ctx import AgentRunCtx
from ralph.agents.invoke._build_command_options import _BuildCommandOptions
from ralph.agents.invoke._choice_menu_option import _ChoiceMenuOption
from ralph.agents.invoke._choice_menu_state import _ChoiceMenuState
from ralph.agents.invoke._invoke_options import InvokeOptions
from ralph.agents.invoke._pty_extras import PtyExtras
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.execution_state import BaseExecutionStrategy
    from ralph.agents.idle_watchdog import TimeoutPolicy, WaitingStatusListener
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.config.models import AgentConfig
    from ralph.process.liveness import LivenessProbe
    from ralph.process.teardown import ProcessTeardown


@dataclass(frozen=True)
class ProcessReaderCtx:
    config: AgentConfig
    policy: TimeoutPolicy
    execution_strategy: BaseExecutionStrategy | None = None
    liveness_probe: LivenessProbe | None = None
    process_teardown: ProcessTeardown | None = None
    waiting_listener: WaitingStatusListener | None = None
    pre_output_listener: Callable[[], None] | None = None
    monitor: WorkspaceMonitor | None = None
    expected_session_id: str | None = None
    workspace_path: Path | None = None
    connectivity_state_provider: Callable[[], str | None] | None = None
    is_waiting_state_provider: Callable[[], bool] | None = None
    completion_is_terminal: Callable[[], bool] | None = None
    input_prompt: str | None = None


__all__ = [
    "AgentRunCtx",
    "InvokeOptions",
    "ProcessReaderCtx",
    "PtyExtras",
    "ResolvedInvocationRuntime",
    "_BuildCommandOptions",
    "_ChoiceMenuOption",
    "_ChoiceMenuState",
]
