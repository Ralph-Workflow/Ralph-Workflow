from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

from ralph.agents.invoke import AgentInactivityTimeoutError, InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.agents.watchdog_fire_reason import WatchdogFireReason
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_finalizer import AttemptFinalization
from ralph.pipeline.attempt_runner import run_agent_attempt
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    invoke_resolution_agent,
)
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps, build_minimal_pipeline_core
from ralph.policy.loader import cached_default_policy_bundle
from ralph.policy.models import AgentChainConfig
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.attempt_outcome import AttemptOutcome


_PRIMARY = "structural-primary"
_FALLBACK = "structural-fallback"
_CHAIN = (_PRIMARY, _FALLBACK)
_ORDINARY_PHASE = "development"

import_module("ralph.pipeline.runner")


@runtime_checkable
class _AttemptFinalizer(Protocol):
    def __call__(self, finalization: AttemptFinalization) -> AttemptOutcome: ...


@dataclass(frozen=True)
class _InvocationObservation:
    launches: tuple[str, ...]
    selected_fallback: str | None
    idle_timeout_seconds: float
    supervision: tuple[bool, float | None]
    status_and_log_lines: tuple[str, ...]
    durable_agent_state: object


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"agent_idle_timeout_seconds": 300.0}})


def _policy_bundle() -> PolicyBundle:
    bundle = cached_default_policy_bundle()
    chains = dict(bundle.agents.agent_chains)
    declared = chains[PHASE_RESOLUTION]
    chains[PHASE_RESOLUTION] = AgentChainConfig(
        agents=list(_CHAIN),
        max_retries=declared.max_retries,
        retry_delay_ms=declared.retry_delay_ms,
    )
    agents_policy = bundle.agents.model_copy(update={"agent_chains": chains})
    return bundle.model_copy(update={"agents": agents_policy})


def _stable(value: object, phase: str) -> object:
    if isinstance(value, dict):
        return {
            str(key).replace(phase, "<phase>"): _stable(item, phase)
            for key, item in value.items()
            if "timestamp" not in str(key).casefold()
        }
    if isinstance(value, list | tuple):
        return tuple(_stable(item, phase) for item in value)
    if isinstance(value, str):
        return value.replace(phase, "<phase>")
    return value


def _invoke(
    route: str,
    tmp_path: Path,
) -> _InvocationObservation:
    phase = _ORDINARY_PHASE if route == "ordinary" else PHASE_RESOLUTION
    config = _config()
    policy_bundle = _policy_bundle()
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, policy_bundle=policy_bundle)
    )
    controller.register_phase_chain(phase, _CHAIN)
    display_context = make_display_context()
    pipeline_deps = PipelineDeps(
        core=build_minimal_pipeline_core(config, display_context),
        policy_bundle=policy_bundle,
        recovery_controller=controller,
        ledger=controller.ledger,
        unavailability_store=controller.unavailability_store,
    )
    launches: list[str] = []
    invocation_shape: list[tuple[float, bool, float | None]] = []
    lines: list[str] = []

    def fake_agent_runner(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineEvent:
        del pipeline_deps, workspace_scope
        launches.append(effect.agent_name)
        invocation_shape.append(
            (
                config.general.agent_idle_timeout_seconds,
                effect.activity_only_supervision,
                effect.activity_only_status_interval_seconds,
            )
        )
        finalizer = kwargs.get("attempt_finalizer")
        if not isinstance(finalizer, _AttemptFinalizer):
            raise AssertionError("shared invocation did not provide an attempt finalizer")
        finalizer(
            AttemptFinalization(
                attempt_ordinal=1,
                attempt_failed=True,
                exc=AgentInactivityTimeoutError(
                    effect.agent_name,
                    300.0,
                    [],
                    InactivityTimeoutOpts(reason=WatchdogFireReason.NO_OUTPUT_AT_START),
                ),
                workspace_changed=False,
                typed_signal=None,
                retryable_reason=None,
            )
        )
        return PipelineEvent.AGENT_FAILURE

    def capture(message: object) -> None:
        raw_record: object = getattr(message, "record", None)
        record: dict[object, object] = raw_record if isinstance(raw_record, dict) else {}
        content = str(record.get("message", ""))
        if content:
            lines.append(content.replace(phase, "<phase>"))

    sink = logger.add(capture, level="INFO", format="{message}")
    token = install_agent_effect_dependencies(
        agent_runner=fake_agent_runner,
        recovery_controller=controller,
        pipeline_deps=pipeline_deps,
    )
    try:
        if route == "ordinary":
            run_agent_attempt(
                AgentEntryPoint.MAIN_PHASE_LOOP,
                effect=InvokeAgentEffect(
                    agent_name=_PRIMARY,
                    phase=phase,
                    prompt_file=str(tmp_path / "prompt.md"),
                    drain=phase,
                ),
                config=config,
                controller=controller,
                deps=AttemptRunnerDeps(clock, PRODUCTION_INVOKE, None, None, None),
                pipeline_deps=pipeline_deps,
                workspace_scope=WorkspaceScope(tmp_path),
                display_context=display_context,
                run_id="structural-parity",
            )
        else:
            invoke_resolution_agent(
                agent_name=_PRIMARY,
                prompt_path=tmp_path / "prompt.md",
                config=config,
                pipeline_deps=pipeline_deps,
                workspace_scope=WorkspaceScope(tmp_path),
                policy_bundle=policy_bundle,
                display=None,
                display_context=display_context,
                session=ResolutionSession(recovery_controller=controller),
            )
    finally:
        reset_agent_effect_dependencies(token)
        logger.remove(sink)

    timeout, activity_only, status_interval = invocation_shape[0]
    selected = controller.preferred_agent_index(phase, _CHAIN).agent
    durable = {
        "ledger": controller.ledger.snapshot(),
        "unavailability": controller.unavailability_store.snapshot(),
    }
    return _InvocationObservation(
        launches=tuple(launches),
        selected_fallback=selected,
        idle_timeout_seconds=timeout,
        supervision=(activity_only, status_interval),
        status_and_log_lines=tuple(lines),
        durable_agent_state=_stable(durable, phase),
    )


def test_resolver_and_ordinary_phase_launch_and_fall_over_identically(tmp_path: Path) -> None:
    ordinary = _invoke("ordinary", tmp_path / "ordinary")
    resolver = _invoke("resolver", tmp_path / "resolver")

    assert ordinary.launches == resolver.launches
    assert ordinary.selected_fallback == resolver.selected_fallback


def test_resolver_and_ordinary_phase_use_identical_idle_timeout(
    tmp_path: Path,
) -> None:
    ordinary = _invoke("ordinary", tmp_path / "ordinary")
    resolver = _invoke("resolver", tmp_path / "resolver")

    assert ordinary.idle_timeout_seconds == resolver.idle_timeout_seconds


def test_resolver_and_ordinary_phase_use_identical_watchdog_supervision(
    tmp_path: Path,
) -> None:
    ordinary = _invoke("ordinary", tmp_path / "ordinary")
    resolver = _invoke("resolver", tmp_path / "resolver")

    assert ordinary.supervision == resolver.supervision


def test_resolver_and_ordinary_phase_emit_and_persist_identically(tmp_path: Path) -> None:
    ordinary = _invoke("ordinary", tmp_path / "ordinary")
    resolver = _invoke("resolver", tmp_path / "resolver")

    assert ordinary.status_and_log_lines == resolver.status_and_log_lines
    assert ordinary.durable_agent_state == resolver.durable_agent_state
