"""Black-box unit tests for :mod:`ralph.pro_support.state_query`."""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, cast
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
from ralph.pro_support.state_query import (
    PipelineStateSnapshot,
    SnapshotRegistry,
    build_pipeline_state_snapshot,
)
from ralph.recovery.controller import RecoveryController
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import UnifiedConfig


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
    return cast("UnifiedConfig", config)


def _load_run_loop() -> object:
    return importlib.import_module("ralph.pipeline.run_loop")


def _load_runner() -> object:
    return importlib.import_module("ralph.pipeline.runner")


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


def test_snapshot_metrics_is_a_plain_dict_copy_of_run_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = PipelineState(phase="planning")
    snapshot = build_pipeline_state_snapshot(state, tmp_path)
    assert isinstance(snapshot.metrics, dict)
    expected = state.metrics.model_dump()
    assert snapshot.metrics == expected
    assert id(snapshot.metrics) != id(expected)


def test_snapshot_iteration_sourced_from_outer_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = PipelineState(phase="planning", outer_progress={"iteration": 4, "other": 1})
    snapshot = build_pipeline_state_snapshot(state, tmp_path)
    assert snapshot.iteration == 4

    state2 = PipelineState(phase="planning")
    snapshot2 = build_pipeline_state_snapshot(state2, tmp_path)
    assert snapshot2.iteration == 0


def test_snapshot_analysis_iteration_sourced_from_loop_iterations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = PipelineState(phase="analysis", loop_iterations={"analysis_iteration": 3, "other": 1})
    snapshot = build_pipeline_state_snapshot(state, tmp_path)
    assert snapshot.analysis_iteration == 3

    state2 = PipelineState(phase="analysis")
    snapshot2 = build_pipeline_state_snapshot(state2, tmp_path)
    assert snapshot2.analysis_iteration == 0


def _attempt_mutation(snap: PipelineStateSnapshot) -> None:
    """Attempt to delete an attribute on a frozen snapshot; the
    frozen-with-slots dataclass raises ``FrozenInstanceError``."""
    delattr(snap, "phase")


def _assign_publish(registry: object, fn: object) -> None:
    """Bind ``fn`` to ``registry.publish`` (typed as object to avoid method-assign)."""
    object.__setattr__(registry, "publish", fn)


def test_snapshot_is_immutable_frozen_dataclass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = PipelineState(phase="planning")
    snapshot = build_pipeline_state_snapshot(state, tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        _attempt_mutation(snapshot)


def test_snapshot_does_not_share_state_with_live_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = PipelineState(phase="planning")
    snapshot = build_pipeline_state_snapshot(state, tmp_path)
    assert snapshot.phase == "planning"
    mutated = state.copy_with(phase="analysis")
    assert snapshot.phase == "planning"
    assert mutated.phase == "analysis"


def test_snapshot_dict_fields_are_copies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = PipelineState(phase="planning", outer_progress={"iteration": 1})
    snapshot = build_pipeline_state_snapshot(state, tmp_path)
    assert snapshot.outer_progress["iteration"] == 1
    new_state = state.copy_with(outer_progress={"iteration": 99})
    assert snapshot.outer_progress["iteration"] == 1
    assert new_state.outer_progress["iteration"] == 99


def test_publish_then_get_latest_round_trip() -> None:
    state = PipelineState(phase="planning")
    snapshot = build_pipeline_state_snapshot(state, Path("/tmp"))
    registry = SnapshotRegistry()
    registry.publish(snapshot)
    latest = registry.get_latest()
    assert latest is not None
    assert latest == snapshot
    assert latest is not snapshot


def test_get_latest_returns_none_when_nothing_published() -> None:
    registry = SnapshotRegistry()
    assert registry.get_latest() is None


def test_run_loop_publishes_snapshot_on_each_reduce_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    runner_module = _load_runner()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    bundle = _fake_bundle()
    initial_state = PipelineState(phase="planning")
    state_after_step = PipelineState(phase="complete")
    registry = SnapshotRegistry()
    publish_calls: list[PipelineStateSnapshot] = []

    real_publish = registry.publish
    capture_publish: list[PipelineStateSnapshot] = []

    def _capturing_publish(snap: PipelineStateSnapshot) -> None:
        publish_calls.append(snap)
        capture_publish.append(snap)
        real_publish(snap)

    _assign_publish(registry, _capturing_publish)

    def _fake_step(**_kwargs: object) -> PipelineState:
        return state_after_step

    monkeypatch.setattr(runner_module, "run_pipeline_step", _fake_step)
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
    _patch_runner_dependencies(monkeypatch, tmp_path, initial_state, bundle)
    _install_display_context(monkeypatch, run_loop_module)
    monkeypatch.setattr(
        runner_module,
        "auto_integrate_on_phase_transition",
        lambda *_args, **_kwargs: None,
    )

    config = _build_config()
    pipeline_deps = make_test_pipeline_deps(
        make_display_context(),
        policy_bundle=bundle,
        snapshot_registry=registry,
    )
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=initial_state, pipeline_deps=pipeline_deps
    )
    assert exit_code == 0
    assert publish_calls, "snapshot was never published"
    assert publish_calls[-1].phase == "complete"
