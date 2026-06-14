"""Dataclass types for agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.invoke._agent_run_ctx import _AgentRunCtx
from ralph.agents.invoke._build_command_options import _BuildCommandOptions
from ralph.agents.invoke._choice_menu_option import _ChoiceMenuOption
from ralph.agents.invoke._choice_menu_state import _ChoiceMenuState
from ralph.agents.invoke._invoke_options import InvokeOptions
from ralph.agents.invoke._pty_extras import _PtyExtras
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.execution_state import GenericExecutionStrategy, OpenCodeExecutionStrategy
    from ralph.agents.idle_watchdog import TimeoutPolicy, WaitingStatusListener
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.config.models import AgentConfig
    from ralph.process.liveness import LivenessProbe


@dataclass(frozen=True)
class _ProcessReaderCtx:
    config: AgentConfig
    policy: TimeoutPolicy
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
    liveness_probe: LivenessProbe | None = None
    waiting_listener: WaitingStatusListener | None = None
    pre_output_listener: Callable[[], None] | None = None
    monitor: WorkspaceMonitor | None = None
    expected_session_id: str | None = None


__all__ = [
    "InvokeOptions",
    "ResolvedInvocationRuntime",
    "_AgentRunCtx",
    "_BuildCommandOptions",
    "_ChoiceMenuOption",
    "_ChoiceMenuState",
    "_ProcessReaderCtx",
    "_PtyExtras",
]
