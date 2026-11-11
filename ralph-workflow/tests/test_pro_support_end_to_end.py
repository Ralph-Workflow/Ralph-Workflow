from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
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
from ralph.pro_support.state_query import (
    PipelineStateSnapshot,
    SnapshotRegistry,
    build_pipeline_state_snapshot,
)
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import UnifiedConfig


def _load_run_loop() -> object:
    return importlib.import_module("ralph.pipeline.run_loop")


def _load_runner() -> object:
    return importlib.import_module("ralph.pipeline.runner")


def _fake_bundle() -> PolicyBundle:
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
            )
        }
    )
    return PolicyBundle(pipeline=pipeline, agents=agents, artifacts=artifacts)


def _seed_workspace(workspace_root: Path) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "PROMPT.md").write_text("# test\n", encoding="utf-8")


def _build_config() -> UnifiedConfig:
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


def _patch_runner_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: PipelineState,
    bundle: PolicyBundle,
) -> None:
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


def _install_display_context(monkeypatch: pytest.MonkeyPatch, run_loop_module: object) -> None:
    ctx = make_display_context()
    runner_module = _load_runner()
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    display = ParallelDisplay(workspace_root=Path("/tmp"), display_context=ctx, is_quiet=True)
    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (display, ctx, lambda: None),
    )


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


@pytest.mark.timeout_seconds(5)
def test_pro_invocation_end_to_end_satisfies_all_three_bullets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    override_bundle = _fake_bundle()
    default_bundle = _fake_bundle()
    registry = SnapshotRegistry()

    recording = _RecordingHeartbeat()

    def _heartbeat_factory(payload: dict[str, object]) -> _RecordingHeartbeat:
        recording.start()
        return recording

    def _watcher_factory(_ws_root: Path) -> _LateMarkerWatcher:
        return _LateMarkerWatcher(
            tmp_path,
            poll_results=[{"run_id": "r", "token": "t", "port": 7432}],
            heartbeat_factory=_heartbeat_factory,
        )

    captured_bundle: list[object] = []

    def _inner_loop(
        live_state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_bundle.append(getattr(ctx, "policy_bundle", None))
        reg = getattr(ctx, "snapshot_registry", None)
        if reg is not None:
            snap = build_pipeline_state_snapshot(live_state, ctx.workspace_scope.root)
            reg.publish(snap)
        return live_state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, default_bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(
        marker_watcher_factory=_watcher_factory,
        snapshot_registry=registry,
        policy_bundle_override=override_bundle,
    )
    exit_code = run_loop_module.run(
        config,
        initial_state=state,
        pro_hooks=hooks,
    )

    assert exit_code == 0

    assert recording.started, "Bullet 1: late marker should have started the heartbeat"
    assert recording.stopped, "Bullet 1: cleanup should have stopped the heartbeat"

    latest = registry.get_latest()
    assert latest is not None, "Bullet 2: snapshot registry must have a published snapshot"
    assert isinstance(latest, PipelineStateSnapshot), (
        f"Bullet 2: expected PipelineStateSnapshot, got {type(latest).__name__}"
    )
    assert latest.phase == "complete", (
        f"Bullet 2: snapshot phase should be 'complete', got {latest.phase!r}"
    )

    assert captured_bundle, "Bullet 3: inner loop did not run"
    assert captured_bundle[0] is override_bundle, (
        "Bullet 3: policy_bundle_override must be observed in ctx.policy_bundle"
    )
    assert captured_bundle[0] is not default_bundle, (
        "Bullet 3: default bundle must NOT be used when override is set"
    )
