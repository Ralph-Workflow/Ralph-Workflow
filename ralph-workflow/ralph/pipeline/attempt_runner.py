from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

from ralph.agents.invoke import attempt_facts_for_failure
from ralph.agents.registry import AgentRegistry
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    EntryPointRunner,
    register_entry_point,
    registered_entry_points,
    registered_runners,
)
from ralph.pipeline.attempt_finalizer import AttemptFinalization
from ralph.pipeline.attempt_result import AttemptResult
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.recovery.agent_config_fingerprint import agent_config_fingerprint
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import RecoveryController
from ralph.recovery.dead_agent_probe import production_dead_agent_probe_runner
from ralph.recovery.false_bench_feedback import FalseBenchFeedback
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityStore
    from ralph.recovery.attempt_outcome import AttemptOutcome


_DEFAULT_CYCLE_CAP = 200
_AGENT_REGISTRY_CACHE_SIZE: Final[int] = 8


class _AttemptInvoke(Protocol):
    def __call__(self, target: object, *args: object, **kwargs: object) -> object: ...


@runtime_checkable
class _RegistryFactoryProvider(Protocol):
    registry_factory: Callable[[object], object] | None


AttemptInvoke = _AttemptInvoke


def build_entry_point_recovery_controller(
    entry_point: AgentEntryPoint,
    *,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle | None,
    deps: AttemptRunnerDeps,
    ledger_snapshot: Mapping[str, object] | None = None,
    false_bench_snapshot: Mapping[str, object] | None = None,
    unavailability_store: UnavailabilityStore | None = None,
    cycle_cap: int | None = None,
    agent_max_consecutive_unproductive: int | None = None,
    agent_max_total_unproductive: int | None = None,
) -> RecoveryController:
    del entry_point
    ledger = (
        AgentFailureLedger.from_snapshot(ledger_snapshot, clock=deps.clock)
        if ledger_snapshot is not None
        else None
    )
    probe_runner = deps.probe_runner
    if probe_runner is None:
        probe_runner = production_dead_agent_probe_runner(config)
    options = RecoveryControllerOptions(
        cycle_cap=cycle_cap if cycle_cap is not None else _DEFAULT_CYCLE_CAP,
        policy_bundle=policy_bundle,
        clock=deps.clock,
        ledger=ledger,
        workspace_probe=deps.workspace_probe,
        unavailability_store=unavailability_store,
        dead_agent_probe_runner=probe_runner,
        false_bench_feedback=FalseBenchFeedback.from_snapshot(false_bench_snapshot),
    )
    if agent_max_consecutive_unproductive is not None or agent_max_total_unproductive is not None:
        options = replace(
            options,
            agent_max_consecutive_unproductive=(
                agent_max_consecutive_unproductive
                if agent_max_consecutive_unproductive is not None
                else options.agent_max_consecutive_unproductive
            ),
            agent_max_total_unproductive=(
                agent_max_total_unproductive
                if agent_max_total_unproductive is not None
                else options.agent_max_total_unproductive
            ),
        )
    return RecoveryController(options=options)


def run_agent_attempt(
    entry_point: AgentEntryPoint,
    *,
    effect: object | None = None,
    invocation: object | None = None,
    config: UnifiedConfig,
    controller: RecoveryController,
    deps: AttemptRunnerDeps,
    state: object | None = None,
    **execute_kwargs: object,
) -> AttemptResult:
    if (effect is None) == (invocation is None):
        raise ValueError("exactly one of effect or invocation is required")
    phase, agent = _attempt_identity(effect, invocation, execute_kwargs)
    fingerprint = _config_fingerprint(config, agent, execute_kwargs)
    session_ref = _session_ref(entry_point, phase, agent, execute_kwargs)
    finalized: dict[int, tuple[AttemptFacts, AttemptOutcome]] = {}
    finalized_failures: list[BaseException] = []

    def finalize(finalization: AttemptFinalization) -> AttemptOutcome:
        recorded = finalized.get(finalization.attempt_ordinal)
        if recorded is not None:
            return recorded[1]
        if finalization.exc is not None:
            finalized_failures.append(finalization.exc)
        facts = _attempt_facts(finalization)
        outcome = controller.finalize_attempt(
            phase,
            agent,
            facts=facts,
            attempt_failed=finalization.attempt_failed,
            typed_signal=finalization.typed_signal,
            exc=finalization.exc,
            retryable_reason=finalization.retryable_reason,
            probe_mode=entry_point is AgentEntryPoint.CANARY_PROBE,
            attempt_key=_attempt_key(
                entry_point, phase, agent, controller.next_attempt_ordinal(phase, agent)
            ),
            config_fingerprint=fingerprint,
            session_ref=session_ref,
        )
        finalized[finalization.attempt_ordinal] = (facts, outcome)
        return outcome

    target = effect if effect is not None else invocation
    invoke_kwargs: dict[str, object] = dict(execute_kwargs)
    invoke_kwargs["attempt_finalizer"] = finalize
    if effect is not None:
        invoke_kwargs["config"] = config
    if state is not None:
        invoke_kwargs["state"] = state
    try:
        event = deps.invoke(target, **invoke_kwargs)
    except BaseException as exc:
        if all(exc is not recorded for recorded in finalized_failures):
            finalize(
                AttemptFinalization(
                    max(finalized, default=0) + 1,
                    True,
                    exc,
                    None,
                    None,
                    None,
                )
            )
        raise
    attempt_failed = _is_agent_failure_event(event)
    ordinal = max(finalized, default=0)
    if not attempt_failed or not finalized:
        ordinal += 1
    recorded = finalized.get(ordinal)
    if recorded is None:
        finalize(AttemptFinalization(ordinal, attempt_failed, None, None, None, None))
        facts, outcome = finalized[ordinal]
    else:
        facts, outcome = recorded
    return AttemptResult(
        event,
        facts,
        outcome,
        tuple((key, value[1]) for key, value in sorted(finalized.items())),
    )


def _attempt_identity(
    effect: object | None,
    invocation: object | None,
    execute_kwargs: Mapping[str, object],
) -> tuple[str, str | None]:
    source = effect if effect is not None else invocation
    effect_phase = ""
    effect_agent: str | None = None
    if isinstance(source, InvokeAgentEffect):
        effect_phase = source.phase
        effect_agent = source.agent_name
    phase = effect_phase or _text_value(execute_kwargs.get("phase")) or ""
    agent = (
        effect_agent
        or _text_value(execute_kwargs.get("agent"))
        or _text_value(execute_kwargs.get("agent_name"))
    )
    return phase, agent


def _attempt_facts(finalization: AttemptFinalization) -> AttemptFacts:
    if finalization.exc is not None:
        facts = attempt_facts_for_failure(finalization.exc)
    else:
        facts = AttemptFacts(artifact_valid=not finalization.attempt_failed)
    if finalization.workspace_changed is not None:
        facts = replace(facts, workspace_changed=finalization.workspace_changed)
    return facts


def _attempt_key(
    entry_point: AgentEntryPoint,
    phase: str,
    agent: str | None,
    attempt_ordinal: int,
) -> str:
    return f"{entry_point.value}:{phase}:{agent}:{attempt_ordinal}"


class _AgentConfigKey:
    __slots__ = ("_config", "_identity")

    def __init__(self, config: UnifiedConfig) -> None:
        self._config = config
        self._identity = id(config)

    def __hash__(self) -> int:
        return self._identity

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _AgentConfigKey) and other._config is self._config


def _agent_registry_for_uncached(config_key: _AgentConfigKey) -> AgentRegistry:
    return AgentRegistry.from_config(config_key._config)


_agent_registry_for = lru_cache(maxsize=_AGENT_REGISTRY_CACHE_SIZE)(_agent_registry_for_uncached)


def _config_fingerprint(
    config: object,
    agent: str | None,
    execute_kwargs: Mapping[str, object],
) -> str | None:
    if agent is None:
        return None
    resolved = _resolve_agent_config(config, agent, execute_kwargs)
    if resolved is None:
        return f"agent:{agent}"
    transport = resolved.transport
    return agent_config_fingerprint(
        agent,
        cmd=resolved.cmd,
        model=resolved.model,
        model_flag=resolved.model_flag,
        json_parser=resolved.json_parser.value,
        transport=transport.value if transport is not None else None,
    )


def _resolve_agent_config(
    config: object,
    agent: str,
    execute_kwargs: Mapping[str, object],
) -> AgentConfig | None:
    registry = _agent_registry(config, execute_kwargs)
    if registry is None:
        return None
    try:
        resolved = registry.get(agent)
    except Exception:
        return None
    return resolved if isinstance(resolved, AgentConfig) else None


def _agent_registry(
    config: object,
    execute_kwargs: Mapping[str, object],
) -> AgentRegistry | None:
    factory = _registry_factory(execute_kwargs)
    if factory is not None:
        try:
            registry = factory(config)
        except Exception:
            registry = None
        if registry is not None:
            return cast("AgentRegistry", registry)
    if not isinstance(config, UnifiedConfig):
        return None
    return _agent_registry_for(_AgentConfigKey(config))


def _registry_factory(
    execute_kwargs: Mapping[str, object],
) -> Callable[[object], object] | None:
    pipeline_deps = execute_kwargs.get("pipeline_deps")
    if not isinstance(pipeline_deps, _RegistryFactoryProvider):
        return None
    factory = pipeline_deps.registry_factory
    if factory is None:
        return None
    return factory


def _is_agent_failure_event(event: object) -> bool:
    value = getattr(event, "value", event)
    return isinstance(value, str) and value == "agent_failure"


def _session_ref(
    entry_point: AgentEntryPoint,
    phase: str,
    agent: str | None,
    execute_kwargs: Mapping[str, object],
) -> dict[str, str]:
    values = (
        ("run_id", _text_value(execute_kwargs.get("run_id"))),
        ("phase", phase or None),
        ("agent", agent),
        ("entry_point", entry_point.value),
        ("session_id", _text_value(execute_kwargs.get("session_id"))),
    )
    return {key: value for key, value in values if value is not None}


def _text_value(value: object | None) -> str | None:
    return value if isinstance(value, str) else None


__all__ = (
    "AttemptInvoke",
    "EntryPointRunner",
    "build_entry_point_recovery_controller",
    "register_entry_point",
    "registered_entry_points",
    "registered_runners",
    "run_agent_attempt",
)
