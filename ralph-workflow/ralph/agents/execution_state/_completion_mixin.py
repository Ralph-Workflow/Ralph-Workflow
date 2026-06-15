"""Completion-enforcing mixin for execution strategies.

Strategies that require explicit completion evidence (terminal ACK, required
artifact, or explicit completion marker) before a clean exit is considered
terminal can inherit this mixin ahead of their base strategy. The mixin
provides a single ``classify_exit`` implementation that delegates the terminal
check to ``_check_signals_terminal`` and is the sole source of truth for
``supports_completion_enforcement()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ._helpers import _check_signals_terminal
from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


class CompletionEnforcingStrategy:
    """Mixin that makes ``classify_exit`` depend on terminal completion signals.

    Host classes must not override ``supports_completion_enforcement()``;
    the mixin itself returns ``True`` and is the sole source of truth for the
    capability.  Overriding the method hides that truth and breaks the
    contract, so it is rejected at class-definition time.
    """

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        class_dict = cast("Mapping[str, object]", cls.__dict__)
        if "supports_completion_enforcement" in class_dict:
            msg = (
                f"{cls.__name__} must not override supports_completion_enforcement(); "
                "CompletionEnforcingStrategy is the sole source of truth"
            )
            raise TypeError(msg)

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: _LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, liveness_probe
        if _check_signals_terminal(completion_signals):
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.RESUMABLE_CONTINUE
