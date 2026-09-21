"""Effect types: what the pipeline wants to do next.

Effects are emitted by the orchestrator and describe the next action
to be taken. They carry all necessary data for the effect handler
to execute the action.

No I/O is performed in this module - effects are pure data descriptions.
"""

from __future__ import annotations

import warnings

from .commit_effect import CommitEffect
from .empty_commit_effect import EmptyCommitEffect
from .exhausted_analysis_phase_advance_effect import ExhaustedAnalysisPhaseAdvanceEffect
from .exit_failure_effect import ExitFailureEffect
from .exit_success_effect import ExitSuccessEffect
from .fan_out_effect import FanOutEffect
from .invoke_agent_effect import InvokeAgentEffect
from .prepare_prompt_effect import PreparePromptEffect
from .push_effect import PushEffect
from .save_checkpoint_effect import SaveCheckpointEffect

__all__ = [
    "CommitEffect",
    "Effect",
    "EmptyCommitEffect",
    "ExhaustedAnalysisPhaseAdvanceEffect",
    "ExitFailureEffect",
    "ExitSuccessEffect",
    "FanOutEffect",
    "InvokeAgentEffect",
    "PreparePromptEffect",
    "PushEffect",
    "SaveCheckpointEffect",
]


def __getattr__(name: str) -> object:
    if name == "FanOutDevelopmentEffect":
        warnings.warn(
            "FanOutDevelopmentEffect is deprecated; use FanOutEffect instead. "
            "# reason: deprecation alias",
            DeprecationWarning,
            stacklevel=2,
        )
        return FanOutEffect
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


Effect = (
    InvokeAgentEffect
    | PreparePromptEffect
    | CommitEffect
    | EmptyCommitEffect
    | ExhaustedAnalysisPhaseAdvanceEffect
    | PushEffect
    | SaveCheckpointEffect
    | ExitSuccessEffect
    | ExitFailureEffect
    | FanOutEffect
)
