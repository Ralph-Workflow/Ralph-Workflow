from __future__ import annotations

from ralph.agents.invoke.attempt_supervision.broken_agent_timeout_observed import (
    BrokenAgentTimeoutObserved,
)
from ralph.agents.invoke.attempt_supervision.completion_observed import CompletionObserved
from ralph.agents.invoke.attempt_supervision.interactive_completion_observed import (
    InteractiveCompletionObserved,
)
from ralph.agents.invoke.attempt_supervision.operator_interrupt_observed import (
    OperatorInterruptObserved,
)
from ralph.agents.invoke.attempt_supervision.post_exit_hang_observed import PostExitHangObserved
from ralph.agents.invoke.attempt_supervision.provider_error_observed import ProviderErrorObserved
from ralph.agents.invoke.attempt_supervision.silence_observed import SilenceObserved
from ralph.agents.invoke.attempt_supervision.startup_failure_observed import StartupFailureObserved
from ralph.agents.invoke.attempt_supervision.terminal_startup_error_observed import (
    TerminalStartupErrorObserved,
)

AttemptObservation = (
    BrokenAgentTimeoutObserved
    | CompletionObserved
    | InteractiveCompletionObserved
    | OperatorInterruptObserved
    | PostExitHangObserved
    | ProviderErrorObserved
    | SilenceObserved
    | StartupFailureObserved
    | TerminalStartupErrorObserved
)

__all__ = ["AttemptObservation"]
