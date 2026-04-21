"""Phases module — phase handlers for the Ralph pipeline.

Each phase is implemented as a module that exports a ``handle_phase`` function.
The handler receives an Effect and a PhaseContext, performs any necessary I/O
(prompt preparation, agent invocation, artifact reading), and emits Events.

Phase handlers are registered by name in ``HANDLERS`` dict. Unknown phases
in the pipeline graph will produce a PhaseHandlerNotFoundError at startup.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel

from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.agents.chain import ChainManager
    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig
    from ralph.policy.models import (
        AgentsPolicy,
        ArtifactsPolicy,
        PipelinePolicy,
    )
    from ralph.workspace.protocol import Workspace


class PhaseContext(BaseModel):  # type: ignore[explicit-any]
    """Context passed to every phase handler.

    Attributes:
        workspace: Workspace for file I/O.
        registry: Agent registry for looking up agent configs.
        chain_manager: Chain manager for drain-to-chain resolution.
        pipeline_policy: Pipeline policy (phase graph).
        agents_policy: Agents policy (chains and drain bindings).
        artifacts_policy: Artifacts policy (artifact contracts).
        config: Optional legacy unified config for backward compatibility.
        console: Rich console for output (optional).
    """

    model_config = {"frozen": True}

    workspace: Workspace
    registry: AgentRegistry
    chain_manager: ChainManager
    pipeline_policy: PipelinePolicy
    agents_policy: AgentsPolicy
    artifacts_policy: ArtifactsPolicy
    config: UnifiedConfig | None = None
    console: Console | None = None


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
PhaseHandler = Callable[["Effect", PhaseContext], list[Event]]


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
    # The effect may be a PreparePromptEffect or InvokeAgentEffect
    # Extract the phase name from the effect
    if isinstance(effect, (InvokeAgentEffect, PreparePromptEffect)):
        phase_name = effect.phase
    else:
        phase_name = "unknown"

    handler = get_handler(phase_name)
    logger.debug("Dispatching to handler for phase: {}", phase_name)
    return handler(effect, ctx)


# Import and register all built-in handlers (imported for side effects only)
from ralph.phases import analysis as _analysis  # noqa: F401,E402
from ralph.phases import commit as _commit  # noqa: F401,E402
from ralph.phases import development as _development  # noqa: F401,E402
from ralph.phases import fix as _fix  # noqa: F401,E402
from ralph.phases import planning as _planning  # noqa: F401,E402
from ralph.phases import review as _review  # noqa: F401,E402

__all__ = [
    "HANDLERS",
    "PhaseContext",
    "PhaseHandlerNotFoundError",
    "get_handler",
    "handle_phase",
    "register_handler",
]
