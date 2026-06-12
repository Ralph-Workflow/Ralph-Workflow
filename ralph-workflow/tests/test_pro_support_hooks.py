"""Black-box unit tests for :mod:`ralph.pro_support.hooks` and ``run`` factory wiring."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

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

    import pytest

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
                json_path=".agent/artifacts/plan.json",
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
    monkeypatch.setattr(
        runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: bundle
    )
    monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "create_initial_state", lambda *_a, **_kw: state)


def _install_display_context(
    monkeypatch: pytest.MonkeyPatch, run_loop_module: object
) -> None:
    ctx = make_display_context()
    runner_module = _load_runner()
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    display = ParallelDisplay(workspace_root=Path("/tmp"), display_context=ctx, is_quiet=True)
    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (display, ctx, lambda: None),
    )


def test_policy_bundle_override_routes_through_inner_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    override_bundle = _fake_bundle()
    factory_calls: list[object] = []

    def _policy_bundle_factory(_scope: object, _cfg: object) -> PolicyBundle:
        factory_calls.append(object())
        return _fake_bundle()

    captured_ctx: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_ctx.append(ctx)
        return _state, "complete", None

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
    _patch_runner_dependencies(monkeypatch, tmp_path, state, _fake_bundle())
    _install_display_context(monkeypatch, run_loop_module)

    hooks = ProPipelineHooks(
        policy_bundle_factory=_policy_bundle_factory,
        policy_bundle_override=override_bundle,
    )

    config = _build_config()
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config,
        initial_state=state,
        pro_hooks=hooks,
    )
    assert exit_code == 0
    assert factory_calls == [], "policy_bundle_factory must NOT be called when override is set"
    assert captured_ctx, "inner loop did not run"
    assert getattr(captured_ctx[0], "policy_bundle", None) is override_bundle


def test_registry_factory_is_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    custom_registry = MagicMock()
    factory_calls: list[object] = []

    def _registry_factory(cfg: object) -> object:
        factory_calls.append(cfg)
        return custom_registry

    captured_registry: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_registry.append(getattr(ctx, "registry", None))
        return _state, "complete", None

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
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(registry_factory=_registry_factory)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    assert len(factory_calls) == 1
    assert factory_calls[0] is config
    assert captured_registry == [custom_registry]


def test_state_factory_is_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    bundle = _fake_bundle()
    custom_state = PipelineState(phase="complete")
    state_factory_calls: list[object] = []

    def _state_factory(
        _cfg: object, _ap: object, _pp: object, _co: object | None
    ) -> PipelineState:
        state_factory_calls.append(object())
        return custom_state

    captured_state: list[PipelineState] = []

    def _inner_loop(
        state: PipelineState, _ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_state.append(state)
        return state, "complete", None

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
    _patch_runner_dependencies(monkeypatch, tmp_path, PipelineState(phase="complete"), bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(state_factory=_state_factory)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(config, pro_hooks=hooks)
    assert exit_code == 0
    assert len(state_factory_calls) == 1
    assert captured_state[0] is custom_state


def test_recovery_controller_factory_is_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    custom_controller = MagicMock(spec=RecoveryController)
    custom_controller.event_bus = MagicMock(subscribe=lambda _cb: lambda: None)
    controller_factory_calls: list[object] = []

    def _controller_factory(
        _st: object, _pb: object, _cfg: object
    ) -> tuple[object, int]:
        controller_factory_calls.append(object())
        return custom_controller, 1

    captured_controller: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_controller.append(getattr(ctx, "controller", None))
        return _state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda *_a, **_kw: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(recovery_controller_factory=_controller_factory)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    assert len(controller_factory_calls) == 1
    assert captured_controller[0] is custom_controller


def test_hooks_default_to_existing_runner_helpers_when_fields_are_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
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
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    exit_code = cast("Callable[..., int]", run_loop_module.run)(config, initial_state=state)
    assert exit_code == 0


def test_snapshot_registry_receives_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    registry = SnapshotRegistry()

    def _inner_loop(
        state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        snap = build_pipeline_state_snapshot(state, tmp_path)
        reg = getattr(ctx, "snapshot_registry", None)
        if reg is not None:
            reg.publish(snap)
        return state, "complete", None

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
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(snapshot_registry=registry)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    latest = registry.get_latest()
    assert latest is not None
    assert isinstance(latest, PipelineStateSnapshot)
    assert latest.phase == "complete"


def test_custom_pipeline_artifact_type_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    custom_bundle = _fake_bundle()

    captured_bundle: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_bundle.append(getattr(ctx, "policy_bundle", None))
        return _state, "complete", None

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
    _patch_runner_dependencies(monkeypatch, tmp_path, state, _fake_bundle())
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(policy_bundle_override=custom_bundle)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    assert captured_bundle[0] is custom_bundle
