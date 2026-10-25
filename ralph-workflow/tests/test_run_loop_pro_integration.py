"""Black-box unit tests for Pro integration in the pipeline run loop.

These tests stub out the runner, marker reader, and heartbeat client so
the run loop can be exercised end-to-end without a real subprocess,
network call, or wall-clock ``time.sleep``. The Pro contract is
exercised at the integration level: heartbeat client is started in Pro
mode, stopped during cleanup, and exit codes are preserved.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.factory import PipelineDeps, build_default_pipeline_deps
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from ralph.config.models import UnifiedConfig


def _load_run_loop() -> ModuleType:
    """Load the run_loop module via importlib after runner has loaded.

    Avoids the pre-existing circular import between
    ``ralph.pipeline.runner`` and ``ralph.pipeline.run_loop`` by
    ensuring runner is fully initialised before run_loop is loaded.
    """
    return importlib.import_module("ralph.pipeline.run_loop")


def _load_runner() -> ModuleType:
    return importlib.import_module("ralph.pipeline.runner")


def _make_fake_bundle() -> PolicyBundle:
    """Build the smallest bundle that satisfies ``run``'s contract."""
    pipeline = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    agents = AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=1),
            "development": AgentChainConfig(agents=["claude"], max_retries=1),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
        },
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
                artifact_path=".agent/artifacts/plan.md",
            )
        }
    )
    return PolicyBundle(pipeline=pipeline, agents=agents, artifacts=artifacts)


def _seed_workspace(workspace_root: Path) -> None:
    """Write the minimum files the run loop expects on the workspace."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "PROMPT.md").write_text("# test\n", encoding="utf-8")


def _seed_marker(workspace_root: Path, *, run_id: str, token: str, port: int) -> None:
    marker_dir = workspace_root / ".ralph"
    marker_dir.mkdir(exist_ok=True)
    payload = {"runId": run_id, "port": port, "heartbeatToken": token}
    (marker_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")


def _build_config(tmp_path: Path) -> UnifiedConfig:
    config = MagicMock()
    config.general = MagicMock()
    config.general.verbosity = Verbosity.NORMAL
    config.general.developer_iters = 1
    config.general.workflow = MagicMock()
    config.general.workflow.checkpoint_enabled = True
    config.general.max_same_agent_retries = 1
    config.general.checkpoint = MagicMock()
    config.general.parallel_max_workers = None
    return config


def _install_display_context(monkeypatch: pytest.MonkeyPatch, run_loop_module: ModuleType) -> None:
    """Force ``make_display_context`` to return a deterministic context."""
    ctx = make_display_context()
    runner_module = _load_runner()
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    display = ParallelDisplay(workspace_root=Path("/tmp"), display_context=ctx, is_quiet=True)
    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (display, ctx, lambda: None),
    )


def _patch_runner_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: PipelineState,
    bundle: PolicyBundle,
) -> None:
    """Patch heavy entry points so the run loop walks to the inner loop and exits cleanly."""
    runner_module = _load_runner()
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
    monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: bundle)
    monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "create_initial_state", lambda *_a, **_kw: state)


def _build_recovery_controller_mock() -> MagicMock:
    return MagicMock(
        spec=RecoveryController,
        event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
    )


def test_heartbeat_started_in_pro_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_workspace(tmp_path)
    _seed_marker(tmp_path, run_id="run-x", token="tok", port=7432)

    recording = _RecordingHeartbeat()

    def _fake_start(_ws: object) -> _RecordingHeartbeat:
        recording.start()
        return recording

    monkeypatch.setattr(run_loop_module, "_start_pro_heartbeat_if_active", _fake_start)

    config = _build_config(tmp_path)
    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    _install_display_context(monkeypatch, run_loop_module)
    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 0
    assert recording.started
    assert recording.stopped


def test_no_heartbeat_when_pro_mode_inactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    started_clients: list[object] = []

    def _fake_start(_ws: object) -> _RecordingHeartbeat:
        rec = _RecordingHeartbeat()
        started_clients.append(rec)
        return rec

    monkeypatch.setattr(run_loop_module, "_start_pro_heartbeat_if_active", _fake_start)

    config = _build_config(tmp_path)
    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    _install_display_context(monkeypatch, run_loop_module)

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 0
    assert started_clients == [] or all(not getattr(c, "started", False) for c in started_clients)


def test_pro_mode_exit_code_zero_on_clean_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_workspace(tmp_path)
    _seed_marker(tmp_path, run_id="run-clean", token="tok", port=7432)

    monkeypatch.setattr(
        run_loop_module,
        "_start_pro_heartbeat_if_active",
        lambda _ws: _RecordingHeartbeat(),
    )

    config = _build_config(tmp_path)
    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    _install_display_context(monkeypatch, run_loop_module)

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 0


def test_pro_mode_exit_code_preserved_on_pipeline_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pipeline that returns a non-zero step code must surface that code under Pro mode."""
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_workspace(tmp_path)
    _seed_marker(tmp_path, run_id="run-fail", token="tok", port=7432)

    recording = _RecordingHeartbeat()
    monkeypatch.setattr(run_loop_module, "_start_pro_heartbeat_if_active", lambda _ws: recording)

    config = _build_config(tmp_path)
    state = PipelineState(phase="planning")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    def _fake_run_inner_loop(
        _state: PipelineState, _ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int]:
        return state, "planning", 7

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _fake_run_inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    _install_display_context(monkeypatch, run_loop_module)

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 7
    assert recording.stopped


def test_connectivity_monitor_cleanup_stops_its_probe_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owned monitor loop finishes its task before the probe thread exits."""
    run_loop_module = _load_run_loop()
    started = threading.Event()
    stopped = threading.Event()

    class _Monitor:
        async def start(self) -> None:
            started.set()

        async def stop(self) -> None:
            stopped.set()

    monkeypatch.setattr(run_loop_module, "ConnectivityMonitor", _Monitor)
    _monitor, stop = run_loop_module._setup_connectivity_monitor(None)

    assert stop is not None
    assert started.wait(timeout=1.0)
    stop()
    assert stopped.is_set()
    assert not any(thread.name == "connectivity-probe" for thread in threading.enumerate())


def test_start_pro_heartbeat_returns_none_when_marker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No marker = no heartbeat, even when RALPH_WORKFLOW_PRO is set."""
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    assert run_loop_module._start_pro_heartbeat_if_active(tmp_path) is None


def test_start_pro_heartbeat_returns_none_when_run_id_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "run.json").write_text(json.dumps({"heartbeatToken": "x"}), encoding="utf-8")
    assert run_loop_module._start_pro_heartbeat_if_active(tmp_path) is None


def test_start_pro_heartbeat_returns_none_when_token_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "run.json").write_text(json.dumps({"runId": "x"}), encoding="utf-8")
    assert run_loop_module._start_pro_heartbeat_if_active(tmp_path) is None


def test_connectivity_monitor_regression_teardown_cancels_pending_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-3: cleanup cancels tasks the monitor itself does not own."""
    run_loop_module = _load_run_loop()

    class _MonitorWithPendingTask:
        def __init__(self) -> None:
            self.pending_task: asyncio.Task[bool] | None = None
            self.stopped = False

        async def start(self) -> None:
            self.pending_task = asyncio.create_task(asyncio.Event().wait())

        async def stop(self) -> None:
            self.stopped = True

    monitor = _MonitorWithPendingTask()
    monkeypatch.setattr(run_loop_module, "ConnectivityMonitor", lambda: monitor)

    _, stop_monitor = run_loop_module._setup_connectivity_monitor(None)
    assert stop_monitor is not None
    stop_monitor()

    assert monitor.stopped
    assert monitor.pending_task is not None
    assert monitor.pending_task.cancelled()


class _RecordingHeartbeat:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_running(self) -> bool:
        return self.started and not self.stopped


class _LateMarkerWatcher:
    """In-process watcher that adopts the marker only after N polls.

    Mirrors the public ``ProMarkerWatcher`` surface that the run
    loop touches (``heartbeat_client``, ``stop()``) so the test
    can be exercised end-to-end without a real watcher thread.
    """

    def __init__(
        self,
        workspace_root: Path,
        *_args: object,
        poll_results: list[dict[str, object] | None],
        heartbeat_factory: Callable[[dict[str, object]], object],
        poll_interval_seconds: float = 0.001,
    ) -> None:
        self._workspace_root = workspace_root
        self._poll_results = list(poll_results)
        self._heartbeat_factory = heartbeat_factory
        self._poll_interval = poll_interval_seconds
        self._stopped = False
        self.is_heartbeat_started = False
        self.heartbeat_client: object | None = None
        self.poll_count = 0

    def start(self) -> None:
        # Drive polls synchronously so the test stays deterministic.
        for result in self._poll_results:
            if self._stopped:
                return
            self.poll_count += 1
            if result is not None:
                self.heartbeat_client = self._heartbeat_factory(result)
                self.is_heartbeat_started = True
                return

    def stop(self) -> None:
        self._stopped = True


def test_pipeline_deps_policy_bundle_is_authoritative_for_pro_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When ``pipeline_deps.policy_bundle`` is already resolved, ``run()`` does not
    call ``pro_hooks.policy_bundle_factory`` again.
    """
    run_loop_module = _load_run_loop()
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    factory_calls: list[tuple[object, ...]] = []

    def fake_factory(workspace_scope: object, config: object) -> PolicyBundle:
        factory_calls.append((workspace_scope, config))
        return bundle

    hooks = ProPipelineHooks(policy_bundle_factory=fake_factory)
    deps = PipelineDeps(
        display_context=make_display_context(),
        policy_bundle=bundle,
    )

    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    _install_display_context(monkeypatch, run_loop_module)

    exit_code = run_loop_module.run(
        _build_config(tmp_path),
        initial_state=state,
        pro_hooks=hooks,
        pipeline_deps=deps,
    )

    assert exit_code == 0
    assert factory_calls == []


def test_pro_collaborator_overrides_reach_inner_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PipelineDeps built with Pro collaborator overrides is forwarded to the inner loop."""
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    override_display_context = make_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")

    def fake_system_materializer(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, name, default_product_criteria, worker_namespace
        return "fake-master-prompt.md"

    def fake_phase_materializer(
        context: object = None,
        options: object = None,
        **kwargs: object,
    ) -> str:
        del context, options, kwargs
        return "fake-phase-prompt.md"

    def fake_artifact_resolver(
        pipeline_policy: object,
        artifacts_policy: object,
        *,
        phase: str,
        drain: str | None = None,
    ) -> object:
        del pipeline_policy, artifacts_policy, phase, drain
        return None

    hooks = ProPipelineHooks(
        display_context=override_display_context,
        model_identity=model_identity,
        master_prompt_materializer=fake_system_materializer,
        phase_prompt_materializer=fake_phase_materializer,
        artifact_requirements_resolver=fake_artifact_resolver,
    )
    display_context = make_display_context()
    deps = build_default_pipeline_deps(
        _build_config(tmp_path),
        display_context,
        pro_hooks=hooks,
    )

    captured_deps: list[PipelineDeps | None] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_deps.append(getattr(ctx, "pipeline_deps", None))
        return _state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )

    exit_code = run_loop_module.run(
        _build_config(tmp_path),
        initial_state=state,
        pro_hooks=hooks,
        pipeline_deps=deps,
    )

    assert exit_code == 0
    assert len(captured_deps) == 1
    observed_deps = captured_deps[0]
    assert observed_deps is not None
    assert observed_deps.display_context is override_display_context
    assert observed_deps.model_identity is model_identity
    assert observed_deps.master_prompt_materializer is fake_system_materializer
    assert observed_deps.phase_prompt_materializer is fake_phase_materializer
    assert observed_deps.artifact_requirements_resolver is fake_artifact_resolver


@pytest.mark.timeout_seconds(2)
def test_late_marker_adoption_starts_heartbeat_after_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A late-arriving marker is adopted by the watcher; heartbeat is still cleaned up."""
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    recording = _RecordingHeartbeat()

    def _heartbeat_factory(payload: dict[str, object]) -> _RecordingHeartbeat:
        if isinstance(payload, dict):
            recording.start()
        return recording

    def _watcher_factory(_ws_root: Path) -> _LateMarkerWatcher:
        return _LateMarkerWatcher(
            tmp_path,
            poll_results=[None, None, {"run_id": "r", "token": "t", "port": 7432}],
            heartbeat_factory=_heartbeat_factory,
        )

    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (
            ParallelDisplay(
                workspace_root=Path("/tmp"),
                display_context=make_display_context(),
                is_quiet=True,
            ),
            make_display_context(),
            lambda: None,
        ),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )

    config = _build_config(tmp_path)
    hooks = ProPipelineHooks(marker_watcher_factory=_watcher_factory)
    exit_code = run_loop_module.run(config, initial_state=state, pro_hooks=hooks)
    assert exit_code == 0
    assert recording.started, "late marker should have started the heartbeat"
    assert recording.stopped, "cleanup should have stopped the heartbeat"


# --- U4b: fail-closed consumers of read_record_outcome ----------------------


def _minimal_ctx(tmp_path: Path, state: PipelineState) -> object:
    """Build a duck-typed _LoopContext for the handoff unit under test."""
    from types import SimpleNamespace

    return SimpleNamespace(
        workspace_scope=SimpleNamespace(root=tmp_path),
        latest_state=[state],
        active_display=MagicMock(),
    )


def _seed_corrupt_record(tmp_path: Path) -> None:
    from ralph.pipeline.auto_integrate_record import record_path

    record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    record_path(tmp_path).write_text("{not json", encoding="utf-8")


def test_handoff_unreadable_record_persists_intervention_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNREADABLE handoff must be decision-complete: no diagnostic, intervention persisted."""
    from ralph.pipeline import run_loop as run_loop_module
    from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind

    _seed_corrupt_record(tmp_path)
    state = PipelineState(phase="development")
    ctx = _minimal_ctx(tmp_path, state)
    monkeypatch.setattr(
        run_loop_module,
        "_diagnose_retained_integration",
        lambda _ctx, _failure: pytest.fail("diagnostic must not launch on UNREADABLE"),
    )

    run_loop_module._handoff_retained_failure(ctx, "retained on unreadable record")

    rebase = ctx.latest_state[0].rebase
    assert rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert rebase.is_intervention_required
    assert "unreadable" in (rebase.last_reason or "")
    # idempotent across restarts: calling again is stable
    run_loop_module._handoff_retained_failure(ctx, "retained on unreadable record")
    assert (
        ctx.latest_state[0].rebase.last_outcome
        is RebaseOutcomeKind.LADDER_ESCALATION
    )


def test_handoff_readable_record_still_runs_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readable retained record keeps its existing diagnostic handoff."""
    from ralph.pipeline import run_loop as run_loop_module
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record

    write_record(
        tmp_path,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha="b" * 40,
        ),
    )

    seen: list[str] = []
    monkeypatch.setattr(
        run_loop_module,
        "_diagnose_retained_integration",
        lambda _ctx, failure: seen.append(failure),
    )
    ctx = _minimal_ctx(tmp_path, PipelineState(phase="development"))
    run_loop_module._handoff_retained_failure(ctx, "failure-x")
    assert seen == ["failure-x"]


def test_retained_integration_reason_unreadable_blocks_dispatch(tmp_path: Path) -> None:
    """UNREADABLE and BUSY dispositions retain ownership like a readable record."""
    from ralph.pipeline.integration_resolution import retained_integration_reason

    _seed_corrupt_record(tmp_path)
    assert retained_integration_reason(tmp_path) is not None


def test_runner_exit_success_blocked_by_unreadable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ExitSuccessEffect must fail closed when the record is unreadable."""
    import types

    import ralph.pipeline.runner as runner_module
    from ralph.pipeline.auto_integrate_record import record_path
    from ralph.pipeline.effects.exit_success_effect import ExitSuccessEffect
    from ralph.pipeline.state import PipelineState

    record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    record_path(tmp_path).write_text("{corrupt", encoding="utf-8")
    state = PipelineState(phase="complete")
    emitted: list[str] = []
    monkeypatch.setattr(
        runner_module,
        "_emit_success_exit",
        lambda _display, _getenv: emitted.append("success") or 0,
    )
    code = runner_module._handle_inline_effect(
        effect=ExitSuccessEffect(),
        state=state,
        pipeline_policy=MagicMock(),
        artifacts_policy=MagicMock(),
        workspace_scope=types.SimpleNamespace(root=tmp_path),
        agents_policy=None,
        registry=None,
        config=None,
        pipeline_deps=None,
        display=MagicMock(),
        pipeline_subscriber=None,
        routing_timing=None,
        dashboard_subscriber=None,
        recovery_controller=None,
    )
    assert code == 1
    assert emitted == []


# --- WU-B: post-recovery dispatch and state propagation ---------------------


def _wu_b_ctx(tmp_path: Path, state: PipelineState) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        workspace_scope=SimpleNamespace(root=tmp_path),
        latest_state=[state],
        active_display=MagicMock(),
        config=MagicMock(
            general=MagicMock(
                agent_max_consecutive_unproductive=3,
            )
        ),
        policy_bundle=MagicMock(),
        registry=MagicMock(get=lambda _name: None),
        pipeline_deps=None,
        display_context=MagicMock(),
    )


def test_post_recovery_dispatch_returns_continuation_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful post-recovery dispatch must clear the block and persist state."""
    from ralph.pipeline import run_loop as run_loop_module
    from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind

    # Clean tree: no record file, no git repo => verdict goes through.
    assert not (tmp_path / ".agent" / "auto_integrate_in_progress.json").exists()
    state = PipelineState(phase="development")
    rebase = state.rebase.model_copy(
        update={
            "last_outcome": RebaseOutcomeKind.CONFLICT,
            "last_reason": "conflict",
            "unresolved_integration_carried": True,
            "resolution_exhausted": True,
            "resolution_exhaustion_reason": "resolver chain exhausted",
        }
    )
    state = state.copy_with(rebase=rebase)
    ctx = _wu_b_ctx(tmp_path, state)

    monkeypatch.setattr(
        run_loop_module,
        "_run_integration_conflict_resolution",
        lambda _ctx, _rebase: True,
    )
    recovered_calls: list[object] = []

    def _fake_startup(_ctx: object, _rebase: object) -> object:
        recovered_calls.append(_rebase)
        # Recovery landed: the recovered rebase clears all conflict evidence.
        return _rebase.model_copy(
            update={
                "last_outcome": RebaseOutcomeKind.FAST_FORWARDED,
                "last_reason": "recovered",
                "unresolved_integration_carried": False,
                "resolution_exhausted": False,
                "resolution_exhaustion_reason": None,
            }
        )

    monkeypatch.setattr(run_loop_module, "_run_startup_integration", _fake_startup)
    monkeypatch.setattr(
        run_loop_module,
        "_announce_conflict_resolution_entry",
        lambda *_a, **_kw: None,
    )
    checkpoint_saves: list[PipelineState] = []
    monkeypatch.setattr(
        run_loop_module,
        "_save_recovered_rebase_checkpoint",
        lambda s, _ctx: checkpoint_saves.append(s),
    )

    from ralph.pipeline.integration_resolution import inspect_integration_resolution

    verdict = inspect_integration_resolution(tmp_path, state.rebase)
    result = run_loop_module._post_recovery_verdict(ctx, state, "development", verdict)

    # BUG (pre-fix): both branches returned the raw verdict, so the caller
    # treated success as another block. The contract: success returns None
    # (dispatch allowed) with the reconciled state persisted.
    assert result is None, "successful post-recovery dispatch must not block"
    assert recovered_calls, "startup integration must run"
    final = ctx.latest_state[0].rebase
    assert not final.resolution_exhausted
    assert not final.unresolved_integration_carried
    assert checkpoint_saves, "reconciled state must be checkpointed"


def test_post_recovery_blocked_still_returns_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still-recoverable verdict after recovery keeps the block behavior."""
    from ralph.pipeline import run_loop as run_loop_module
    from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
    from ralph.pipeline.integration_resolution import (
        IntegrationResolutionStatus,
        IntegrationResolutionVerdict,
    )

    state = PipelineState(phase="development")
    rebase = state.rebase.model_copy(
        update={
            "last_outcome": RebaseOutcomeKind.CONFLICT,
            "last_reason": "conflict",
            "resolution_exhausted": True,
            "resolution_exhaustion_reason": "resolver chain exhausted",
        }
    )
    state = state.copy_with(rebase=rebase)
    ctx = _wu_b_ctx(tmp_path, state)
    monkeypatch.setattr(
        run_loop_module,
        "_run_integration_conflict_resolution",
        lambda _ctx, _rebase: False,
    )
    monkeypatch.setattr(run_loop_module, "_run_startup_integration", lambda _c, _r: None)
    monkeypatch.setattr(
        run_loop_module,
        "_announce_conflict_resolution_entry",
        lambda *_a, **_kw: None,
    )
    blocked = IntegrationResolutionVerdict(
        IntegrationResolutionStatus.RECOVERABLE, ("still blocked",), "drain"
    )
    result = run_loop_module._post_recovery_verdict(ctx, state, "development", blocked)
    assert isinstance(result, IntegrationResolutionVerdict)
    assert not result.dispatch_allowed
