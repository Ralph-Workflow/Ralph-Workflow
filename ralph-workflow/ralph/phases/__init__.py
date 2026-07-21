"""Phases module — phase handlers for the Ralph pipeline.

Each phase is implemented as a module that exports a ``handle_phase`` function.
The handler receives an Effect and a PhaseContext, performs any necessary I/O
(prompt preparation, agent invocation, artifact reading), and emits Events.

Phase handlers are registered by name in ``HANDLERS`` dict. Unknown phases
in the pipeline graph will produce a PhaseHandlerNotFoundError at startup.

Two registration mechanisms are supported:
1. Decorator-based at import time: @register_handler("phase_name")
2. Role-based at policy-load time: register_role_handlers(policy)

The role-based mechanism registers the generic handler for every phase whose
role matches a known role class (commit or analysis). This allows policy-renamed
phases to work without hardcoded handler registration.

Import ordering note: ``register_role_handlers`` must be importable before any
module that ultimately loads ``ralph.policy.loader`` (which imports this
function during policy loading). Concretely, ``ralph.policy.loader`` is reached
via ``ralph.phases.analysis -> ralph.phases.artifacts -> ralph.pipeline ->
ralph.config -> ralph.policy``. To break that cycle, the phase-module imports
are deferred to call time inside ``register_role_handlers``. The phase modules
were already imported once during the policy load chain, so they are available
when ``register_role_handlers`` is actually invoked.

``PhaseContext`` is exposed at the package level via the ``__getattr__`` lazy
loader below so that ``from ralph.phases import PhaseContext`` still works
after the cycle has unwound.

``InvokeAgentEffect`` and ``PreparePromptEffect`` are similarly imported
lazily inside ``handle_phase`` because ``ralph.pipeline.effects`` also sits
on the same import cycle (via ``ralph.pipeline`` -> ``ralph.config`` ->
``ralph.policy`` -> ``ralph.policy.loader``). They are runtime values for
an ``isinstance`` check, so a lazy import inside the function keeps the
top-of-file declarations cycle-free without changing observable behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ralph.phases.phase_context import PhaseContext
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.events import Event
    from ralph.policy.models import PipelinePolicy


class PhaseHandlerNotFoundError(Exception):
    """Raised when no handler is registered for a phase.

    Attributes:
        phase: Phase name that has no handler.
    """

    def __init__(self, phase: str) -> None:
        self.phase = phase
        msg = (
            f"No handler registered for phase '{phase}'. "
            f"Register the handler in ralph/phases/__init__.py "
            f"or use a built-in phase name."
        )
        super().__init__(msg)


# Phase handler signature: takes Effect and PhaseContext, returns list of Events
PhaseHandler = Callable[["Effect", "PhaseContext"], list["Event"]]


# Registry of built-in phase handlers
HANDLERS: dict[str, PhaseHandler] = {}


def register_handler(phase_name: str) -> Callable[[PhaseHandler], PhaseHandler]:
    """Decorator to register a phase handler.

    Usage::

        @register_handler("planning")
        def handle_planning(effect: Effect, ctx: PhaseContext) -> list[Event]:
            ...

    Args:
        phase_name: Name of the phase this handler handles.

    Returns:
        Decorator function.
    """

    def decorator(func: PhaseHandler) -> PhaseHandler:
        HANDLERS[phase_name] = func
        return func

    return decorator


def register_role_handlers(policy: PipelinePolicy) -> None:
    """Register generic handlers for policy-declared role-based phases.

    Called at policy-load time to ensure every phase with a recognized role has
    a handler registered, even if the phase name is not one of the canonical
    built-in names.

    - Execution-role phases are mapped to the generic ``handle_execution_phase``.
    - Commit-role phases are mapped to the generic ``handle_commit_phase``.
    - Analysis-role phases are mapped to the generic ``handle_generic_analysis_phase``.
    - Review-role phases are mapped to the generic ``handle_review``.
    - Verification-role phases are mapped to the generic ``handle_verification_phase``.

    Phases already registered via ``@register_handler`` are not overwritten.

    The phase module imports are deferred to call time: ``ralph.policy.loader``
    imports this function during policy loading, and importing the phase modules
    at module load time would re-enter that same loading chain. By the time
    policy loading finishes and this function is actually called, all phase
    modules are fully available as a side effect of the load.

    Args:
        policy: Loaded pipeline policy.
    """
    from ralph.phases import analysis as _analysis
    from ralph.phases import commit as _commit
    from ralph.phases import commit_cleanup as _commit_cleanup
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
        elif phase_def.role == "commit_cleanup" and phase_name not in HANDLERS:
            HANDLERS[phase_name] = _commit_cleanup.handle_commit_cleanup_phase


def get_handler(phase_name: str) -> PhaseHandler:
    """Get the handler for a phase.

    Args:
        phase_name: Name of the phase.

    Returns:
        Phase handler function.

    Raises:
        PhaseHandlerNotFoundError: If no handler is registered.
    """
    handler = HANDLERS.get(phase_name)
    if handler is None:
        raise PhaseHandlerNotFoundError(phase_name)
    return handler


def handle_phase(
    effect: Effect,
    ctx: PhaseContext,
) -> list[Event]:
    """Dispatch to the appropriate phase handler.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit to the reducer.

    Raises:
        PhaseHandlerNotFoundError: If no handler is registered for the phase.
    """
    # The effect may be a PreparePromptEffect or InvokeAgentEffect. Both
    # types are imported lazily so the ralph.phases -> ralph.pipeline ->
    # ralph.config -> ralph.policy -> ralph.policy.loader -> ralph.phases
    # import cycle does not re-enter an unfinished ``ralph.phases`` module
    # load when this module is first imported. By the time ``handle_phase``
    # is called, the cycle has unwound and these imports are safe.
    from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect

    if isinstance(effect, InvokeAgentEffect | PreparePromptEffect):
        phase_name = effect.phase
    else:
        phase_name = "unknown"

    handler = get_handler(phase_name)
    logger.debug("Dispatching to handler for phase: {}", phase_name)
    return handler(effect, ctx)


def __getattr__(name: str) -> object:
    """Lazily expose ``PhaseContext`` to break the ralph.phases import cycle.

    Importing ``ralph.phases.phase_context`` at package import time would pull
    ``ralph.phases.__init__`` back into its own loading chain and re-enter
    ``ralph.policy.loader`` mid-load. Defer the resolution to attribute access
    so direct imports from ``ralph.phases`` and from any downstream consumer
    continue to work without re-entering the cycle.
    """
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
