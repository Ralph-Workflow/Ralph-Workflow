from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.pipeline.attempt_runner import AttemptInvoke
from ralph.pipeline.effect_executor import execute_agent_effect

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.recovery.connectivity_state import ConnectivityState
    from ralph.workspace.probe import WorkspaceChangeProbe


@dataclass(frozen=True)
class AttemptRunnerDeps:
    clock: Clock
    invoke: AttemptInvoke
    probe_runner: Callable[[str, str], ProbeOutcome | None] | None
    workspace_probe: WorkspaceChangeProbe | None
    connectivity: Callable[[], ConnectivityState | None] | None


PRODUCTION_INVOKE: AttemptInvoke = cast("AttemptInvoke", execute_agent_effect)


__all__ = ("PRODUCTION_INVOKE", "AttemptInvoke", "AttemptRunnerDeps")
