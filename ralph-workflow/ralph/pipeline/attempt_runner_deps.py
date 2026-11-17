from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.agents.invoke import AttemptEnded, InvokeOptions
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext
from ralph.pipeline.attempt_runner import AttemptInvoke
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import AgentFailureEvent, PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from ralph.agents.clock import Clock
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.recovery.connectivity_state import ConnectivityState
    from ralph.workspace.probe import WorkspaceChangeProbe


@runtime_checkable
class _EffectRunner(Protocol):
    def __call__(
        self,
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *,
        display_context: DisplayContext | None,
        **opts: object,
    ) -> PipelineEvent | AgentFailureEvent | AttemptEnded: ...


EffectRunner = _EffectRunner


def _as_effect_runner(value: object) -> _EffectRunner:
    if not isinstance(value, _EffectRunner):
        raise TypeError("effect runner must satisfy EffectRunner")
    return value


class _RawAgentInvoke(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None: ...


RawAgentInvoke = _RawAgentInvoke


def _forward_effect(
    runner: _EffectRunner,
    target: InvokeAgentEffect,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    display_context: DisplayContext | None,
    extras: Mapping[str, object],
) -> object:
    return runner(
        target,
        config,
        pipeline_deps,
        workspace_scope,
        display_context=display_context,
        **extras,
    )


def effect_attempt_invoke(runner: _EffectRunner) -> AttemptInvoke:
    def _invoke(
        target: InvokeAgentEffect | AgentConfig,
        *args: object,
        **kwargs: object,
    ) -> object:
        if not isinstance(target, InvokeAgentEffect):
            raise TypeError("effect attempts require an InvokeAgentEffect target")
        positional = list(args)
        extras = dict(kwargs)
        config = extras.pop("config", None)
        pipeline_deps = extras.pop("pipeline_deps", None)
        workspace_scope = extras.pop("workspace_scope", None)
        if config is None and positional:
            config = positional.pop(0)
        if pipeline_deps is None and positional:
            pipeline_deps = positional.pop(0)
        if workspace_scope is None and positional:
            workspace_scope = positional.pop(0)
        display_context: DisplayContext | None = None
        if "display_context" in extras:
            candidate = extras.pop("display_context")
            if candidate is not None and not isinstance(candidate, DisplayContext):
                raise TypeError("effect attempts require DisplayContext or None")
            display_context = candidate
        if not isinstance(config, UnifiedConfig):
            raise TypeError("effect attempts require a UnifiedConfig")
        if not isinstance(pipeline_deps, PipelineDeps):
            raise TypeError("effect attempts require PipelineDeps")
        if not isinstance(workspace_scope, WorkspaceScope):
            raise TypeError("effect attempts require WorkspaceScope")
        if display_context is not None and not isinstance(display_context, DisplayContext):
            raise TypeError("effect attempts require DisplayContext or None")
        if positional:
            raise TypeError("effect attempts do not accept extra positional arguments")
        return _forward_effect(
            runner,
            target,
            config,
            pipeline_deps,
            workspace_scope,
            display_context,
            extras,
        )

    return _invoke


def _drained_carry_value(lines: Iterator[str]) -> object:
    iterator = iter(lines)
    while True:
        try:
            next(iterator)
        except StopIteration as stop:
            carry_value: object = stop.value
            return carry_value


def raw_attempt_invoke(invoke: _RawAgentInvoke) -> AttemptInvoke:
    def _invoke(
        target: InvokeAgentEffect | AgentConfig,
        *_args: object,
        **kwargs: object,
    ) -> object:
        if not isinstance(target, AgentConfig):
            raise TypeError("raw attempts require an AgentConfig target")
        prompt_file = kwargs.pop("prompt_file")
        if not isinstance(prompt_file, str):
            raise TypeError("raw attempts require a string prompt_file keyword")
        options = kwargs.pop("options", None)
        if options is not None and not isinstance(options, InvokeOptions):
            raise TypeError("raw attempts require InvokeOptions or None")
        result = invoke(target, prompt_file, options=options)
        if result is None:
            return PipelineEvent.AGENT_SUCCESS
        carry_value = _drained_carry_value(result)
        if isinstance(carry_value, AttemptEnded):
            carried_facts = carry_value.facts
            if carried_facts is not None:
                carry_value.facts = replace(carried_facts, artifact_valid=True)
            return carry_value
        return PipelineEvent.AGENT_SUCCESS

    return _invoke


@dataclass(frozen=True)
class AttemptRunnerDeps:
    clock: Clock
    invoke: AttemptInvoke
    probe_runner: Callable[[str, str], ProbeOutcome | None] | None
    workspace_probe: WorkspaceChangeProbe | None
    connectivity: Callable[[], ConnectivityState | None] | None


PRODUCTION_INVOKE: AttemptInvoke = effect_attempt_invoke(
    _as_effect_runner(execute_agent_effect)
)


__all__ = (
    "PRODUCTION_INVOKE",
    "AttemptInvoke",
    "AttemptRunnerDeps",
    "EffectRunner",
    "RawAgentInvoke",
    "effect_attempt_invoke",
    "raw_attempt_invoke",
)
