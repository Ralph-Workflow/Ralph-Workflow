
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeGuard

from loguru import logger

if TYPE_CHECKING:
    from ralph.phases.phase_context import PhaseContext
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.events import Event
    from ralph.policy.models import PipelinePolicy


class PhaseHandlerNotFoundError(Exception):

    def __init__(self, phase: str) -> None:
        self.phase = phase
        msg = (
            f"No handler registered for phase '{phase}'. "
            f"Register the handler in ralph/phases/__init__.py "
            f"or use a built-in phase name."
        )
        super().__init__(msg)


PhaseHandler = Callable[["Effect", "PhaseContext"], list["Event"]]


def _is_phase_handler(value: object) -> TypeGuard[PhaseHandler]:
    return callable(value)


HANDLERS: dict[
    str, PhaseHandler
] = {}


def register_handler(phase_name: str) -> Callable[[PhaseHandler], PhaseHandler]:

    def decorator(func: PhaseHandler) -> PhaseHandler:
        HANDLERS[phase_name] = func
        return func

    return decorator


def register_role_handlers(policy: PipelinePolicy) -> None:
    from ralph.phases import analysis as _analysis
    from ralph.phases import commit as _commit
    from ralph.phases import commit_review as _commit_review
    from ralph.phases import execution as _execution
    from ralph.phases import review as _review
    from ralph.phases import verification as _verification

    for phase_name, phase_def in policy.phases.items():
        if phase_def.role == "execution" and phase_name not in HANDLERS:
            HANDLERS[phase_name] = _execution.handle_execution_phase
        elif phase_def.role == "commit" and phase_name not in HANDLERS:
            HANDLERS[phase_name] = _commit.handle_commit_phase
        elif phase_def.role == "analysis" and phase_name not in HANDLERS:
            HANDLERS[phase_name] = _analysis.handle_generic_analysis_phase
        elif phase_def.role == "review" and phase_name not in HANDLERS:
            HANDLERS[phase_name] = _review.handle_review
        elif phase_def.role == "verification" and phase_name not in HANDLERS:
            HANDLERS[phase_name] = _verification.handle_verification_phase
        elif phase_def.role == "commit_review" and phase_name not in HANDLERS:
            commit_review_handler = _commit_review.handle_commit_review_phase
            if _is_phase_handler(commit_review_handler):
                HANDLERS[phase_name] = commit_review_handler


def get_handler(phase_name: str) -> PhaseHandler:
    handler = HANDLERS.get(phase_name)
    if handler is None:
        raise PhaseHandlerNotFoundError(phase_name)
    return handler


def handle_phase(
    effect: Effect,
    ctx: PhaseContext,
) -> list[Event]:
    from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect

    if isinstance(effect, InvokeAgentEffect | PreparePromptEffect):
        phase_name = effect.phase
    else:
        phase_name = "unknown"

    handler = get_handler(phase_name)
    logger.debug("Dispatching to handler for phase: {}", phase_name)
    return handler(effect, ctx)


def __getattr__(name: str) -> object:
    if name == "PhaseContext":
        from ralph.phases.phase_context import PhaseContext

        return PhaseContext
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


__all__ = [
    "HANDLERS",
    "PhaseContext",
    "PhaseHandlerNotFoundError",
    "get_handler",
    "handle_phase",
    "register_handler",
    "register_role_handlers",
]
