"""Integration tests for fan-out wiring in pipeline runner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Never

import ralph.prompts.materialize
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.protocol.env import WORKER_NAMESPACE_ENV
from ralph.pipeline import fan_out as fan_out_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseParallelization,
    PhaseTransition,
    PhaseWorkflowFallback,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEvent, FalloverEvent
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import pytest

    from ralph.pipeline.events import Event


def _legacy_display() -> runner_module.ParallelDisplay:
    return runner_module.ParallelDisplay(make_display_context())


def _make_work_unit(unit_id: str, *, step_ids: tuple[str, ...] = ()) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=[f"src/{unit_id}"],
        step_ids=list(step_ids),
    )


def _make_policy_bundle(
    max_workers: int = 4,
    *,
    workflow_fallback_target: str | None = None,
    extra_phases: Mapping[str, PhaseDefinition] | None = None,
) -> PolicyBundle:
    """Build a REAL policy bundle for the fan-out wiring tests.

    The reducer reads ``policy.phases`` and
    ``policy.phases.get(state.phase).workflow_fallback``. A stub answers
    those reads with whatever it likes, and the reducer then adopted the
    stub's auto-created attribute as the next phase -- a phase no policy
    declares. Every phase here is a real ``PhaseDefinition`` and every
    route a real ``PhaseTransition``, so the reducer only ever sees
    values a real policy can produce.
    """
    phases: dict[str, PhaseDefinition] = {
        "development": PhaseDefinition(
            drain="development",
            role="execution",
            transitions=PhaseTransition(
                on_success="planning",
                on_failure="failed_terminal",
                on_loopback="development",
            ),
            parallelization=PhaseParallelization(
                max_parallel_workers=max_workers,
                post_fanout_verification=False,
            ),
            workflow_fallback=(
                PhaseWorkflowFallback(target=workflow_fallback_target)
                if workflow_fallback_target is not None
                else None
            ),
        ),
        "planning": PhaseDefinition(
            drain="planning",
            role="execution",
            transitions=PhaseTransition(on_success="complete", on_failure="failed_terminal"),
        ),
        "complete": PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete"),
        ),
        "failed_terminal": PhaseDefinition(
            drain="failed_terminal",
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success="failed_terminal"),
        ),
    }
    if extra_phases is not None:
        phases.update(extra_phases)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(
            failed_route="failed_terminal",
            terminal_failure_phase="failed_terminal",
        ),
    )
    agents = AgentsPolicy(
        agent_drains={
            "development": AgentDrainConfig(chain="developer", drain_class="development"),
            "planning": AgentDrainConfig(chain="planner", drain_class="planning"),
        },
        agent_chains={
            "developer": AgentChainConfig(agents=["developer"]),
            "planner": AgentChainConfig(agents=["planner"]),
        },
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())


def _bundle_with_phantom_fallback_target() -> PolicyBundle:
    """Build a policy whose fallback target is NOT a declared phase.

    ``PipelinePolicy`` rejects such a policy when it is loaded, so the
    misconfiguration is simulated by dropping the target phase from an
    otherwise valid policy. The reducer is the LAST gate before the run
    adopts a phase value, so it must refuse the target on its own: a
    fallback to a phase that does not exist would move the run into a
    phase nothing can route.
    """
    bundle = _make_policy_bundle(workflow_fallback_target="planning")
    remaining = {
        name: phase for name, phase in bundle.pipeline.phases.items() if name != "planning"
    }
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"phases": remaining})}
    )


def _recovery_controller(bundle: PolicyBundle) -> RecoveryController:
    """Build the run's recovery owner for the given policy."""
    return RecoveryController(options=RecoveryControllerOptions(policy_bundle=bundle))


def _crashing_fan_out_state() -> PipelineState:
    """Return the state a development fan-out starts from."""
    return PipelineState(
        phase="development",
        work_units=(_make_work_unit("unit-a"),),
        phase_chains={"development": AgentChainState(agents=["developer"])},
    )


def _install_crashing_coordinator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the fan-out coordinator raise the way an unexpected crash does."""

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _boom(**kwargs: object) -> Never:
        del kwargs
        raise RuntimeError("fanout exploded")

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _boom)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)


class TestFanOutRouting:
    """Test that runner routes correctly based on work_units."""

    def test_serial_when_no_work_units(self, tmp_path: Path) -> None:
        state = PipelineState(phase="development", work_units=())
        policy_bundle = _make_policy_bundle()

        effect = runner_module.determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root=tmp_path),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "development"

    def test_fanout_when_work_units_present(self) -> None:
        """When work_units present, development phase uses FanOutEffect."""
        units = (
            _make_work_unit("unit-a"),
            _make_work_unit("unit-b"),
        )
        max_workers = 3
        state = PipelineState(phase="development", work_units=units)
        policy_bundle = _make_policy_bundle(max_workers=max_workers)

        effect = runner_module.determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, FanOutEffect)
        assert effect.work_units == units
        assert effect.max_workers == max_workers

    def test_serial_when_single_work_unit(self) -> None:
        """Single work_unit falls through to InvokeAgentEffect — fan-out requires >=2 units."""
        state = PipelineState(phase="development", work_units=(_make_work_unit("unit-a"),))
        policy_bundle = _make_policy_bundle()

        effect = runner_module.determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "development"

    def test_non_development_phase_not_affected(self) -> None:
        """Other phases always use InvokeAgentEffect regardless of work_units."""
        units = (_make_work_unit("unit-a"),)
        state = PipelineState(phase="planning", work_units=units)
        policy_bundle = _make_policy_bundle()

        effect = runner_module.determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "planning"


def test_execute_fan_out_sync_wires_signal_handlers_and_same_workspace_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    units = (_make_work_unit("unit-a"), _make_work_unit("unit-b"))
    effect = FanOutEffect(work_units=units, max_workers=2)
    state = PipelineState(
        phase="development",
        work_units=units,
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    policy_bundle = _make_policy_bundle(max_workers=2)
    workspace_scope = WorkspaceScope(tmp_path)
    install_calls: list[tuple[object, object, object]] = []
    coordinator_calls: list[dict[str, object]] = []
    executor_calls: list[dict[str, object]] = []
    mcp_factory_calls: list[dict[str, object]] = []

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            executor_calls.append(
                {
                    "command": tuple(command),
                    "signal_bridge": signal_bridge,
                }
            )

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            mcp_factory_calls.append({"workspace": workspace})

    def _fake_install(loop: object, root_task: object, bridge: object) -> None:
        install_calls.append((loop, root_task, bridge))

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        coordinator_calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(runner_module, "install_signal_handlers", _fake_install)
    monkeypatch.setattr(runner_module, "SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(runner_module, "DynamicBindingMcpServerFactory", _FakeMcpFactory)
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert len(install_calls) == 1
    assert len(executor_calls) == 1
    assert executor_calls[0]["signal_bridge"] is install_calls[0][2]
    assert mcp_factory_calls
    ctx = coordinator_calls[0]["ctx"]
    assert ctx.same_workspace is not None
    # Verify session contract fields are properly threaded from the runner's
    # build_session_mcp_plan_for_phase into SameWorkspaceContext.
    assert ctx.same_workspace.session_drain == "development"
    assert "media.read" in ctx.same_workspace.session_capabilities
    assert ctx.same_workspace.session_model_identity is not None
    assert ctx.same_workspace.session_capability_profile is not None
    assert ctx.same_workspace.worker_commands["unit-a"] == (
        fan_out_module.sys.executable,
        "-m",
        "ralph",
        "--parallel-worker-manifest",
        str(tmp_path / ".agent" / "workers" / "unit-a" / "worker-manifest.json"),
    )
    assert (
        ctx.same_workspace.worker_commands["unit-a"] != ctx.same_workspace.worker_commands["unit-b"]
    )
    assert (
        ctx.same_workspace.worker_manifest_paths["unit-a"]
        != ctx.same_workspace.worker_manifest_paths["unit-b"]
    )


def test_execute_fan_out_sync_persists_worker_manifests_with_distinct_phase_and_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unit = _make_work_unit("unit-a", step_ids=("S-7", "S-8"))
    effect = FanOutEffect(work_units=(unit,), max_workers=1, phase="parallel-dev")
    state = PipelineState(
        phase="parallel-dev",
        work_units=(unit,),
        phase_chains={"parallel-dev": AgentChainState(agents=["claude"])},
    )
    parallel_phase = PhaseDefinition(
        drain="development",
        role="execution",
        transitions=PhaseTransition(on_success="complete", on_failure="failed_terminal"),
        parallelization=PhaseParallelization(
            max_parallel_workers=1,
            post_fanout_verification=False,
        ),
    )
    policy_bundle = _make_policy_bundle(
        max_workers=1,
        extra_phases={"parallel-dev": parallel_phase},
    )
    workspace_scope = WorkspaceScope(tmp_path)
    coordinator_calls: list[dict[str, object]] = []

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        coordinator_calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    ctx = coordinator_calls[0]["ctx"]
    same_workspace = ctx.same_workspace
    assert same_workspace is not None
    manifest_path = same_workspace.worker_manifest_paths[unit.unit_id]
    manifest = ParallelWorkerManifest.load(manifest_path)
    assert manifest.phase == "parallel-dev"
    assert manifest.drain == "development"
    assert manifest.step_ids == ["S-7", "S-8"]
    assert manifest.to_work_unit().step_ids == ["S-7", "S-8"]
    assert manifest.worker_namespace == str(tmp_path / ".agent" / "workers" / unit.unit_id)


def test_execute_fan_out_sync_converts_unexpected_coordinator_error_to_failed_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A crashed fan-out is charged to the recovery owner, exactly like a crashed step.

    The owner is the single component allowed to decide whether the
    agent may try again, so the crash reaches it instead of the reducer
    deciding on its own. The owner's verdict is one bounded retry in
    the same phase: the phase stays put, the chain records exactly one
    attempt, and the crash is reported through ``last_error``.
    """
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    policy_bundle = _make_policy_bundle(max_workers=1)
    state = _crashing_fan_out_state()
    workspace_scope = WorkspaceScope(tmp_path)
    _install_crashing_coordinator(monkeypatch)

    controller = _recovery_controller(policy_bundle)
    owner_events: list[FailureEvent | FalloverEvent] = []
    controller.event_bus.subscribe(owner_events.append)
    pipeline_deps = PipelineDeps(
        display_context=make_display_context(),
        recovery_controller=controller,
    )

    recovered = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        pipeline_deps=pipeline_deps,
    )

    assert recovered.phase == "development"
    development_chain = recovered.chain_for_phase("development")
    assert development_chain is not None
    assert development_chain.retries == 1
    assert recovered.recovery_epoch == 0
    assert recovered.last_error is not None
    assert "Fan-out execution crashed" in recovered.last_error
    assert "fanout exploded" in recovered.last_error
    # The crash reached the OWNER: it published the failure for the
    # crashed phase, so the attempt was charged there and not to a
    # reducer-local retry counter.
    assert any(
        isinstance(evt, FailureEvent)
        and evt.phase == "development"
        and "Fan-out execution crashed" in evt.reason
        for evt in owner_events
    ), f"the owner was never told about the crash: {owner_events}"


def test_execute_fan_out_sync_without_recovery_owner_fails_closed_without_a_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With no owner reachable the reducer grants NO attempt of its own.

    The reducer holds no verdict to apply when no recovery owner is
    reachable, so it must not decide that the agent may try again:
    granting one would make the reducer a SECOND decision owner. The
    phase therefore takes the policy's failure route instead of
    retrying an agent nobody has judged, and no retry is recorded on
    the chain.
    """
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    policy_bundle = _make_policy_bundle(max_workers=1)
    state = _crashing_fan_out_state()
    workspace_scope = WorkspaceScope(tmp_path)
    _install_crashing_coordinator(monkeypatch)

    recovered = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    # The phase reaches the policy's declared failure route -- a REAL
    # phase, never a value the reducer invented.
    assert recovered.phase == policy_bundle.pipeline.recovery.failed_route
    assert recovered.phase in policy_bundle.pipeline.phases
    development_chain = recovered.chain_for_phase("development")
    assert development_chain is not None
    assert development_chain.retries == 0
    assert recovered.last_error is not None
    assert "Fan-out execution crashed" in recovered.last_error
    assert "fanout exploded" in recovered.last_error


def test_execute_fan_out_sync_without_owner_honours_a_declared_workflow_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A REAL declared fallback target is honoured, and still grants no retry.

    The fallback is the policy's own route for an exhausted agent
    chain, so the reducer follows it. What matters for the owner
    contract is the second half: the reducer still grants no attempt of
    its own, because nobody could judge the agent.
    """
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    policy_bundle = _make_policy_bundle(max_workers=1, workflow_fallback_target="planning")
    state = _crashing_fan_out_state()
    workspace_scope = WorkspaceScope(tmp_path)
    _install_crashing_coordinator(monkeypatch)

    recovered = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert recovered.phase == "planning"
    assert recovered.phase in policy_bundle.pipeline.phases
    development_chain = recovered.chain_for_phase("development")
    assert development_chain is not None
    assert development_chain.retries == 0
    assert recovered.last_error is not None
    assert "Fan-out execution crashed" in recovered.last_error
    assert "fanout exploded" in recovered.last_error


def test_execute_fan_out_sync_without_owner_keeps_the_phase_the_policy_declares(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No owner + a policy whose fallback is the phase itself: the phase stays.

    The declared fallback is the policy's own route for an exhausted
    agent chain, so the reducer follows it and the phase does not move.
    The point of the case is the second half of the contract: the
    reducer granted NO attempt of its own, because nobody could judge
    the agent.
    """
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    policy_bundle = _make_policy_bundle(max_workers=1, workflow_fallback_target="development")
    state = _crashing_fan_out_state()
    workspace_scope = WorkspaceScope(tmp_path)
    _install_crashing_coordinator(monkeypatch)

    recovered = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert recovered.phase == "development"
    assert recovered.phase in policy_bundle.pipeline.phases
    development_chain = recovered.chain_for_phase("development")
    assert development_chain is not None
    assert development_chain.retries == 0
    assert recovered.last_error is not None
    assert "Fan-out execution crashed" in recovered.last_error
    assert "fanout exploded" in recovered.last_error


def test_execute_fan_out_sync_never_adopts_a_fallback_target_that_is_not_a_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fallback to a phase the policy does not declare must not move the run.

    The reducer is the last gate before the run adopts a phase value.
    A target that names no declared phase would move the run into a
    phase nothing can route, so the failure takes the ordinary failure
    route instead.
    """
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    policy_bundle = _bundle_with_phantom_fallback_target()
    state = _crashing_fan_out_state()
    workspace_scope = WorkspaceScope(tmp_path)
    _install_crashing_coordinator(monkeypatch)

    recovered = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert recovered.phase != "planning"
    assert recovered.phase == policy_bundle.pipeline.recovery.failed_route
    assert recovered.phase in policy_bundle.pipeline.phases
    development_chain = recovered.chain_for_phase("development")
    assert development_chain is not None
    assert development_chain.retries == 0
    assert recovered.last_error is not None
    assert "Fan-out execution crashed" in recovered.last_error


def test_execute_fan_out_sync_requeues_running_workers_via_reducer_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resumed_event = getattr(PipelineEvent, "WORKERS_RESUMED", None)
    assert resumed_event is not None

    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(
        phase="development",
        work_units=(unit,),
        worker_states={"unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.RUNNING)},
    )
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    seen_events: list[object] = []
    original_reduce = runner_module.reducer_reduce

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        del kwargs
        return []

    def _recording_reduce(
        current_state: PipelineState,
        event: Event,
        pipeline_policy: PipelinePolicy | None = None,
        **_kwargs: object,
    ) -> tuple[PipelineState, object]:
        seen_events.append(event)
        return original_reduce(current_state, event, pipeline_policy)

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module, "reducer_reduce", _recording_reduce)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    result = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert seen_events[0] == resumed_event
    assert result.worker_states["unit-a"].status == WorkerStatus.PENDING


def test_execute_fan_out_sync_uses_parallel_display_subscriber_when_not_provided(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase="development", work_units=(unit,))
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    notified_phases: list[str] = []

    class _Subscriber:
        def notify(self, state: PipelineState) -> None:
            notified_phases.append(state.phase)

    class _FakeParallelDisplay:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.subscriber = _Subscriber()

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[PipelineEvent]:
        del kwargs
        return [PipelineEvent.AGENT_SUCCESS]

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.display.parallel_display.ParallelDisplay", _FakeParallelDisplay)
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda current_state, *_args, **_kwargs: (current_state, None),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        dashboard_subscriber=None,
    )

    # WORKERS_RESUMED notification + one per fan_out_events entry (AGENT_SUCCESS)
    assert notified_phases == [state.phase, state.phase]


def test_execute_fan_out_sync_notifies_dashboard_subscriber_after_each_reduce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase="development", work_units=(unit,))
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    reduced_phases: list[str] = []
    notified_phases: list[str] = []

    class _Subscriber:
        def notify(self, state: PipelineState) -> None:
            notified_phases.append(state.phase)

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[PipelineEvent]:
        del kwargs
        return [PipelineEvent.AGENT_SUCCESS]

    def _recording_reduce(
        current_state: PipelineState,
        event: object,
        pipeline_policy: object | None = None,
        **_kwargs: object,
    ) -> tuple[PipelineState, None]:
        del event, pipeline_policy, _kwargs
        next_state = current_state.copy_with(phase=current_state.phase)
        reduced_phases.append(next_state.phase)
        return next_state, None

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module, "reducer_reduce", _recording_reduce)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)

    runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        dashboard_subscriber=_Subscriber(),
    )

    assert notified_phases == reduced_phases


def test_materialize_prepared_prompt_uses_worker_namespace_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When RALPH_WORKER_NAMESPACE is set, prompt payloads land in the worker's namespace."""

    worker_ns = tmp_path / ".agent" / "workers" / "unit-test"
    worker_ns.mkdir(parents=True, exist_ok=True)
    shared_payload_dir = tmp_path / ".agent" / "tmp" / "prompt_payloads"

    recorded_kwargs: list[dict[str, object]] = []

    def _fake_materialize(**kwargs: object) -> str:
        recorded_kwargs.append(dict(kwargs))
        return "rendered-prompt"

    def _fake_dump(workspace: object, phase: object, prompt: object) -> str:
        del workspace, phase, prompt
        return "/fake/prompt/path"

    # Patch materialize and dump to avoid writing files

    monkeypatch.setattr(ralph.prompts.materialize, "dump_rendered_prompt", _fake_dump)

    policy = load_policy(tmp_path / ".agent")
    workspace_scope = WorkspaceScope(tmp_path)
    effect = PreparePromptEffect(phase="development", iteration=1)
    pipeline_deps = PipelineDeps(
        display_context=make_display_context(),
        phase_prompt_materializer=_fake_materialize,
    )

    runner_module.materialize_prepared_prompt(
        effect,
        policy.pipeline,
        policy.artifacts,
        workspace_scope,
        env={str(WORKER_NAMESPACE_ENV): str(worker_ns)},
        pipeline_deps=pipeline_deps,
    )

    assert len(recorded_kwargs) == 1
    wn = recorded_kwargs[0].get("worker_namespace")
    assert wn is not None, "worker_namespace must be passed to materialize_prompt_for_phase"
    assert wn == worker_ns, f"Expected {worker_ns}, got {wn}"
    assert not shared_payload_dir.exists(), (
        "Shared payload dir must not be written when worker_namespace is set"
    )


def test_materialize_prepared_prompt_no_namespace_without_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without RALPH_WORKER_NAMESPACE, worker_namespace is None (shared path used)."""

    monkeypatch.delenv(str(WORKER_NAMESPACE_ENV), raising=False)
    recorded_kwargs: list[dict[str, object]] = []

    def _fake_materialize(**kwargs: object) -> str:
        recorded_kwargs.append(dict(kwargs))
        return "rendered-prompt"

    monkeypatch.setattr(ralph.prompts.materialize, "dump_rendered_prompt", lambda *a, **k: "/f")

    policy = load_policy(tmp_path / ".agent")
    workspace_scope = WorkspaceScope(tmp_path)
    effect = PreparePromptEffect(phase="development", iteration=1)
    pipeline_deps = PipelineDeps(
        display_context=make_display_context(),
        phase_prompt_materializer=_fake_materialize,
    )

    runner_module.materialize_prepared_prompt(
        effect,
        policy.pipeline,
        policy.artifacts,
        workspace_scope,
        pipeline_deps=pipeline_deps,
    )

    assert len(recorded_kwargs) == 1
    assert recorded_kwargs[0].get("worker_namespace") is None
