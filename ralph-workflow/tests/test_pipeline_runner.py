"""Black-box tests for run_loop.run consuming PipelineDeps.

Uses injected fakes only: no real subprocess, no real network, no time.sleep,
no real file I/O.
"""

from __future__ import annotations

import importlib
import time
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.config.enums import Verbosity
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME
from ralph.pipeline.factory import PipelineDeps
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
    return importlib.import_module("ralph.pipeline.run_loop")


def _load_runner() -> ModuleType:
    return importlib.import_module("ralph.pipeline.runner")


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
    return cast("UnifiedConfig", config)


def _display_context() -> DisplayContext:
    return make_display_context(
        console=Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    )


def _make_fake_bundle() -> PolicyBundle:
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
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    agents = AgentsPolicy(
        agent_chains={"planning": AgentChainConfig(agents=["claude"], max_retries=1)},
        agent_drains={"planning": AgentDrainConfig(chain="planning")},
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
                json_path=".agent/artifacts/plan.md",
            )
        }
    )
    return PolicyBundle(pipeline=pipeline, agents=agents, artifacts=artifacts)


def _build_recovery_controller_mock() -> MagicMock:
    return MagicMock(
        spec=RecoveryController,
        event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
    )


def _make_default_state(*_args: object, **_kwargs: object) -> PipelineState:
    return PipelineState(phase="complete")


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


class TestRunLoopPipelineDeps:
    """Tests for run_loop.run(pipeline_deps=...)."""

    def test_run_with_pipeline_deps_none_uses_agent_registry_from_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        runner_module = _load_runner()
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

        state = PipelineState(phase="complete")
        bundle = _make_fake_bundle()
        _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

        config = _build_config(tmp_path)
        from_config_spy = runner_module.AgentRegistry.from_config
        from_config_spy.reset_mock()

        exit_code = cast("Callable[..., int]", run_loop_module.run)(
            config,
            initial_state=state,
            pipeline_deps=None,
        )

        assert exit_code == 0
        from_config_spy.assert_called_once_with(config)

    def test_run_with_pipeline_deps_routes_registry_through_fake_factory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        runner_module = _load_runner()
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

        state = PipelineState(phase="complete")
        bundle = _make_fake_bundle()
        _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

        fake_registry = MagicMock()
        fake_registry_factory = MagicMock(return_value=fake_registry)
        deps = PipelineDeps(
            display_context=_display_context(),
            registry_factory=fake_registry_factory,
        )

        config = _build_config(tmp_path)
        from_config_spy = runner_module.AgentRegistry.from_config
        from_config_spy.reset_mock()

        exit_code = cast("Callable[..., int]", run_loop_module.run)(
            config,
            initial_state=state,
            pipeline_deps=deps,
        )

        assert exit_code == 0
        fake_registry_factory.assert_called_once_with(config)
        from_config_spy.assert_not_called()

    def test_run_with_pro_hooks_and_pipeline_deps_prefers_pipeline_deps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        runner_module = _load_runner()
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

        state = PipelineState(phase="complete")
        bundle = _make_fake_bundle()
        _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

        pro_registry = MagicMock()
        pro_registry_factory = MagicMock(return_value=pro_registry)
        deps_registry = MagicMock()
        deps_registry_factory = MagicMock(return_value=deps_registry)

        pro_bundle = _make_fake_bundle()
        deps_bundle = _make_fake_bundle()
        pro_policy_factory = MagicMock(return_value=pro_bundle)
        deps_policy_factory = MagicMock(return_value=deps_bundle)
        load_spy = MagicMock(return_value=bundle)
        monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", load_spy)

        pro_hooks = ProPipelineHooks(
            policy_bundle_override=pro_bundle,
            policy_bundle_factory=pro_policy_factory,
            registry_factory=pro_registry_factory,
        )
        deps = PipelineDeps(
            display_context=_display_context(),
            policy_bundle=deps_bundle,
            policy_bundle_factory=deps_policy_factory,
            registry_factory=deps_registry_factory,
        )

        config = _build_config(tmp_path)
        from_config_spy = runner_module.AgentRegistry.from_config
        from_config_spy.reset_mock()

        exit_code = cast("Callable[..., int]", run_loop_module.run)(
            config,
            initial_state=state,
            pro_hooks=pro_hooks,
            pipeline_deps=deps,
        )

        assert exit_code == 0
        deps_registry_factory.assert_called_once_with(config)
        pro_registry_factory.assert_not_called()
        from_config_spy.assert_not_called()
        pro_policy_factory.assert_not_called()
        deps_policy_factory.assert_not_called()
        load_spy.assert_not_called()


def _capture_run_ctx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    initial_state: PipelineState | None = None,
    provide_initial_state: bool = True,
    **run_kwargs: object,
) -> tuple[object, PipelineState]:
    """Run ``run_loop.run`` with heavy dependencies stubbed and return (ctx, state)."""
    run_loop_module = _load_run_loop()
    runner_module = _load_runner()

    state = initial_state or PipelineState(phase="complete")
    bundle = _make_fake_bundle()

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
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    # Prevent the legacy heartbeat helper from invoking the watcher a second time.
    monkeypatch.setattr(run_loop_module, "_start_pro_heartbeat_if_active", lambda _ws: None)

    captured: list[tuple[PipelineState, object, str]] = []

    def _fake_inner_loop(
        inner_state: PipelineState, ctx: object, _prev: str
    ) -> tuple[PipelineState, str, None]:
        captured.append((inner_state, ctx, _prev))
        return inner_state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _fake_inner_loop)

    config = _build_config(tmp_path)
    run_args: dict[str, object] = {}
    if provide_initial_state:
        run_args["initial_state"] = state
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config,
        **run_args,
        **run_kwargs,
    )
    assert exit_code == 0
    assert len(captured) == 1
    return captured[0][1], captured[0][0]


class TestInjectionPrecedence:
    """Tests verifying injection precedence: pipeline_deps > pro_hooks > defaults."""

    def test_pipeline_deps_registry_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        pro_registry = MagicMock()
        deps_registry = MagicMock()
        pro_hooks = ProPipelineHooks(registry_factory=MagicMock(return_value=pro_registry))
        deps = PipelineDeps(
            display_context=_display_context(),
            registry_factory=MagicMock(return_value=deps_registry),
        )

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pipeline_deps=deps, pro_hooks=pro_hooks)

        assert ctx.registry is deps_registry
        assert not pro_hooks.registry_factory.called

    def test_pro_hooks_registry_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runner_module = _load_runner()
        pro_registry = MagicMock()
        pro_hooks = ProPipelineHooks(registry_factory=MagicMock(return_value=pro_registry))

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pro_hooks=pro_hooks)

        assert ctx.registry is pro_registry
        assert not runner_module.AgentRegistry.from_config.called

    def test_registry_default_used_when_nothing_provided(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runner_module = _load_runner()

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path)

        assert ctx.registry is runner_module.AgentRegistry.from_config.return_value
        assert runner_module.AgentRegistry.from_config.called

    def test_pipeline_deps_policy_bundle_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        deps_bundle = _make_fake_bundle()
        pro_bundle = _make_fake_bundle()
        pro_hooks = ProPipelineHooks(
            policy_bundle_override=pro_bundle,
            policy_bundle_factory=MagicMock(return_value=pro_bundle),
        )
        deps = PipelineDeps(
            display_context=_display_context(),
            policy_bundle=deps_bundle,
        )

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pipeline_deps=deps, pro_hooks=pro_hooks)

        assert ctx.policy_bundle is deps_bundle

    def test_pipeline_deps_policy_bundle_factory_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        deps_bundle = _make_fake_bundle()
        pro_bundle = _make_fake_bundle()
        deps_factory = MagicMock(return_value=deps_bundle)
        pro_hooks = ProPipelineHooks(
            policy_bundle_override=pro_bundle,
            policy_bundle_factory=MagicMock(return_value=pro_bundle),
        )
        deps = PipelineDeps(
            display_context=_display_context(),
            policy_bundle_factory=deps_factory,
        )

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pipeline_deps=deps, pro_hooks=pro_hooks)

        assert ctx.policy_bundle is deps_bundle
        deps_factory.assert_called_once()
        assert not pro_hooks.policy_bundle_factory.called

    def test_pro_hooks_policy_bundle_override_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runner_module = _load_runner()
        pro_bundle = _make_fake_bundle()
        pro_hooks = ProPipelineHooks(policy_bundle_override=pro_bundle)
        load_spy = MagicMock(return_value=_make_fake_bundle())
        monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", load_spy)

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pro_hooks=pro_hooks)

        assert ctx.policy_bundle is pro_bundle
        load_spy.assert_not_called()

    def test_pro_hooks_policy_bundle_factory_wins_over_kwarg_and_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runner_module = _load_runner()
        pro_bundle = _make_fake_bundle()
        kwarg_bundle = _make_fake_bundle()
        pro_hooks = ProPipelineHooks(policy_bundle_factory=MagicMock(return_value=pro_bundle))
        kwarg_factory = MagicMock(return_value=kwarg_bundle)
        load_spy = MagicMock(return_value=_make_fake_bundle())
        monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", load_spy)

        ctx, _ = _capture_run_ctx(
            monkeypatch,
            tmp_path,
            pro_hooks=pro_hooks,
            policy_bundle_factory=kwarg_factory,
        )

        assert ctx.policy_bundle is pro_bundle
        pro_hooks.policy_bundle_factory.assert_called_once()
        kwarg_factory.assert_not_called()
        load_spy.assert_not_called()

    def test_policy_bundle_kwarg_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runner_module = _load_runner()
        kwarg_bundle = _make_fake_bundle()
        kwarg_factory = MagicMock(return_value=kwarg_bundle)
        load_spy = MagicMock(return_value=_make_fake_bundle())
        monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", load_spy)

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, policy_bundle_factory=kwarg_factory)

        assert ctx.policy_bundle is kwarg_bundle
        load_spy.assert_not_called()

    def test_pipeline_deps_state_factory_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        deps_state = PipelineState(phase="complete")
        pro_state = PipelineState(phase="complete")
        pro_hooks = ProPipelineHooks(state_factory=MagicMock(return_value=pro_state))
        deps = PipelineDeps(
            display_context=_display_context(),
            state_factory=MagicMock(return_value=deps_state),
        )

        _, state = _capture_run_ctx(
            monkeypatch,
            tmp_path,
            pipeline_deps=deps,
            pro_hooks=pro_hooks,
            provide_initial_state=False,
        )

        assert state is deps_state
        assert not pro_hooks.state_factory.called

    def test_pro_hooks_state_factory_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runner_module = _load_runner()
        pro_state = PipelineState(phase="complete")
        pro_hooks = ProPipelineHooks(state_factory=MagicMock(return_value=pro_state))
        create_spy = MagicMock(return_value=PipelineState(phase="complete"))
        monkeypatch.setattr(runner_module, "create_initial_state", create_spy)

        _, state = _capture_run_ctx(
            monkeypatch, tmp_path, pro_hooks=pro_hooks, provide_initial_state=False
        )

        assert state is pro_state
        create_spy.assert_not_called()

    def test_state_kwarg_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runner_module = _load_runner()
        kwarg_state = PipelineState(phase="complete")
        kwarg_factory = MagicMock(return_value=kwarg_state)
        create_spy = MagicMock(return_value=PipelineState(phase="complete"))
        monkeypatch.setattr(runner_module, "create_initial_state", create_spy)

        _, state = _capture_run_ctx(
            monkeypatch,
            tmp_path,
            state_factory=kwarg_factory,
            provide_initial_state=False,
        )

        assert state is kwarg_state
        create_spy.assert_not_called()

    def test_pipeline_deps_recovery_controller_factory_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        runner_module = _load_runner()
        pro_controller = _build_recovery_controller_mock()
        deps_controller = _build_recovery_controller_mock()
        pro_hooks = ProPipelineHooks(
            recovery_controller_factory=MagicMock(return_value=(pro_controller, 1))
        )
        deps = PipelineDeps(
            display_context=_display_context(),
            recovery_controller_factory=MagicMock(return_value=(deps_controller, 1)),
        )

        default_called = False

        def _default_build(_state: object, _pp: object, _cfg: object) -> tuple[object, int]:
            nonlocal default_called
            default_called = True
            return _build_recovery_controller_mock(), 1

        monkeypatch.setattr(run_loop_module, "_build_recovery_controller", _default_build)
        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
        )
        monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
        monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
        monkeypatch.setattr(
            runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: _make_fake_bundle()
        )
        monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
        monkeypatch.setattr(
            runner_module,
            "AgentRegistry",
            MagicMock(from_config=MagicMock(return_value=MagicMock())),
        )
        monkeypatch.setattr(runner_module, "create_initial_state", _make_default_state)
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

        captured_ctx: list[object] = []

        def _fake_inner_loop(
            inner_state: PipelineState, ctx: object, _prev: str
        ) -> tuple[PipelineState, str, None]:
            captured_ctx.append(ctx)
            return inner_state, "complete", None

        monkeypatch.setattr(run_loop_module, "_run_inner_loop", _fake_inner_loop)

        config = _build_config(tmp_path)
        exit_code = run_loop_module.run(
            config,
            pipeline_deps=deps,
            pro_hooks=pro_hooks,
        )

        assert exit_code == 0
        assert len(captured_ctx) == 1
        assert captured_ctx[0].controller is deps_controller
        assert not pro_hooks.recovery_controller_factory.called
        assert not default_called

    def test_pro_hooks_recovery_controller_factory_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        runner_module = _load_runner()
        pro_controller = _build_recovery_controller_mock()
        pro_hooks = ProPipelineHooks(
            recovery_controller_factory=MagicMock(return_value=(pro_controller, 1))
        )

        default_called = False

        def _default_build(_state: object, _pp: object, _cfg: object) -> tuple[object, int]:
            nonlocal default_called
            default_called = True
            return _build_recovery_controller_mock(), 1

        monkeypatch.setattr(run_loop_module, "_build_recovery_controller", _default_build)
        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
        )
        monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
        monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
        monkeypatch.setattr(
            runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: _make_fake_bundle()
        )
        monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
        monkeypatch.setattr(
            runner_module,
            "AgentRegistry",
            MagicMock(from_config=MagicMock(return_value=MagicMock())),
        )
        monkeypatch.setattr(runner_module, "create_initial_state", _make_default_state)
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

        captured_ctx: list[object] = []

        def _fake_inner_loop(
            inner_state: PipelineState, ctx: object, _prev: str
        ) -> tuple[PipelineState, str, None]:
            captured_ctx.append(ctx)
            return inner_state, "complete", None

        monkeypatch.setattr(run_loop_module, "_run_inner_loop", _fake_inner_loop)

        config = _build_config(tmp_path)
        exit_code = run_loop_module.run(
            config,
            pro_hooks=pro_hooks,
        )

        assert exit_code == 0
        assert len(captured_ctx) == 1
        assert captured_ctx[0].controller is pro_controller
        assert not default_called

    def test_recovery_controller_kwarg_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        runner_module = _load_runner()
        kwarg_controller = _build_recovery_controller_mock()
        kwarg_factory = MagicMock(return_value=(kwarg_controller, 1))

        default_called = False

        def _default_build(_state: object, _pp: object, _cfg: object) -> tuple[object, int]:
            nonlocal default_called
            default_called = True
            return _build_recovery_controller_mock(), 1

        monkeypatch.setattr(run_loop_module, "_build_recovery_controller", _default_build)
        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
        )
        monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
        monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
        monkeypatch.setattr(
            runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: _make_fake_bundle()
        )
        monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
        monkeypatch.setattr(
            runner_module,
            "AgentRegistry",
            MagicMock(from_config=MagicMock(return_value=MagicMock())),
        )
        monkeypatch.setattr(runner_module, "create_initial_state", _make_default_state)
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

        captured_ctx: list[object] = []

        def _fake_inner_loop(
            inner_state: PipelineState, ctx: object, _prev: str
        ) -> tuple[PipelineState, str, None]:
            captured_ctx.append(ctx)
            return inner_state, "complete", None

        monkeypatch.setattr(run_loop_module, "_run_inner_loop", _fake_inner_loop)

        config = _build_config(tmp_path)
        exit_code = run_loop_module.run(
            config,
            recovery_controller_factory=kwarg_factory,
        )

        assert exit_code == 0
        assert len(captured_ctx) == 1
        assert captured_ctx[0].controller is kwarg_controller
        assert not default_called

    def test_pipeline_deps_marker_watcher_factory_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        deps_factory = MagicMock()
        pro_factory = MagicMock()
        pro_hooks = ProPipelineHooks(marker_watcher_factory=pro_factory)
        deps = PipelineDeps(
            display_context=_display_context(),
            marker_watcher_factory=deps_factory,
        )

        captured_factories: list[object] = []

        def _fake_start_watcher(
            _workspace_root: Path, *, watcher_factory: object = None
        ) -> tuple[object, object]:
            captured_factories.append(watcher_factory)
            return None, None

        monkeypatch.setattr(run_loop_module, "_start_pro_marker_watcher", _fake_start_watcher)

        _ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pipeline_deps=deps, pro_hooks=pro_hooks)

        assert captured_factories == [deps_factory]

    def test_pro_hooks_marker_watcher_factory_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        pro_factory = MagicMock()
        pro_hooks = ProPipelineHooks(marker_watcher_factory=pro_factory)

        captured_factories: list[object] = []

        def _fake_start_watcher(
            _workspace_root: Path, *, watcher_factory: object = None
        ) -> tuple[object, object]:
            captured_factories.append(watcher_factory)
            return None, None

        monkeypatch.setattr(run_loop_module, "_start_pro_marker_watcher", _fake_start_watcher)

        _ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pro_hooks=pro_hooks)

        assert captured_factories == [pro_factory]

    def test_marker_watcher_kwarg_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_loop_module = _load_run_loop()
        kwarg_factory = MagicMock()

        captured_factories: list[object] = []

        def _fake_start_watcher(
            _workspace_root: Path, *, watcher_factory: object = None
        ) -> tuple[object, object]:
            captured_factories.append(watcher_factory)
            return None, None

        monkeypatch.setattr(run_loop_module, "_start_pro_marker_watcher", _fake_start_watcher)

        _ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, marker_watcher_factory=kwarg_factory)

        assert captured_factories == [kwarg_factory]

    def test_pipeline_deps_snapshot_registry_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        deps_registry = object()
        pro_registry = object()
        pro_hooks = ProPipelineHooks(snapshot_registry=pro_registry)
        deps = PipelineDeps(
            display_context=_display_context(),
            snapshot_registry=deps_registry,
        )

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pipeline_deps=deps, pro_hooks=pro_hooks)

        assert ctx.snapshot_registry is deps_registry

    def test_pro_hooks_snapshot_registry_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        pro_registry = object()
        pro_hooks = ProPipelineHooks(snapshot_registry=pro_registry)

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pro_hooks=pro_hooks)

        assert ctx.snapshot_registry is pro_registry

    def test_snapshot_registry_kwarg_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        kwarg_registry = object()

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, snapshot_registry=kwarg_registry)

        assert ctx.snapshot_registry is kwarg_registry

    def test_recovery_sleep_kwarg_rejected_with_pipeline_deps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        deps = PipelineDeps(display_context=_display_context())

        with pytest.raises(ValueError, match="Passing factory kwargs alongside pipeline_deps"):
            _capture_run_ctx(
                monkeypatch,
                tmp_path,
                pipeline_deps=deps,
                _recovery_sleep=lambda _seconds: None,
            )

    def test_recovery_sleep_kwarg_used_without_pipeline_deps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def _custom_sleep(_seconds: float) -> None:
            return None

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, _recovery_sleep=_custom_sleep)

        assert ctx.sleep is _custom_sleep

    def test_recovery_sleep_defaults_to_time_sleep(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path)

        assert ctx.sleep is time.sleep

    def test_pipeline_deps_recovery_sleep_wins_over_pro_hooks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def deps_sleep(_seconds: float) -> None:
            return None

        def pro_sleep(_seconds: float) -> None:
            return None

        pro_hooks = ProPipelineHooks(recovery_sleep=pro_sleep)
        deps = PipelineDeps(
            display_context=_display_context(),
            recovery_sleep=deps_sleep,
        )

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pipeline_deps=deps, pro_hooks=pro_hooks)

        assert ctx.sleep is deps_sleep

    def test_pro_hooks_recovery_sleep_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def pro_sleep(_seconds: float) -> None:
            return None

        pro_hooks = ProPipelineHooks(recovery_sleep=pro_sleep)

        ctx, _ = _capture_run_ctx(monkeypatch, tmp_path, pro_hooks=pro_hooks)

        assert ctx.sleep is pro_sleep

    @pytest.mark.parametrize(
        "kwarg_name, kwarg_value",
        [
            (
                "policy_bundle_factory",
                lambda _ws, _cfg: _make_fake_bundle(),
            ),
            ("registry_factory", lambda _cfg: MagicMock()),
            (
                "state_factory",
                lambda _cfg, _agents, _pipeline, _overrides: PipelineState(phase="complete"),
            ),
            (
                "recovery_controller_factory",
                lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
            ),
            ("marker_watcher_factory", lambda _path: MagicMock()),
            ("snapshot_registry", object()),
            ("_recovery_sleep", lambda _seconds: None),
        ],
    )
    def test_factory_kwarg_rejected_when_pipeline_deps_provided(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        kwarg_name: str,
        kwarg_value: object,
    ) -> None:
        deps = PipelineDeps(display_context=_display_context())

        with pytest.raises(ValueError, match="Passing factory kwargs alongside pipeline_deps"):
            _capture_run_ctx(
                monkeypatch,
                tmp_path,
                pipeline_deps=deps,
                **{kwarg_name: kwarg_value},
            )


# --- AC-04: lifecycle refresh at the runner seam -------------------------


def test_runner_wires_pre_post_refresh_around_development_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-04: the public runner seam in ``ralph/pipeline/runner.py``
    must call ``before_agent_refresh`` exactly once BEFORE invoking
    the agent and ``after_agent_refresh`` exactly once AFTER, but
    only for the development/fix execution drain. Planning,
    review, and commit drains must observe no refresh calls.

    The test drives the actual runner code (not direct hook
    imports) by calling ``_invoke_execute_effect_with_optional_display``
    with a fake ``InvokeAgentEffect`` and patches the lifecycle
    hooks so the test observes the observable event sequence.
    """
    from ralph.mcp.explore import lifecycle as lifecycle_module
    from ralph.mcp.explore.lifecycle import LifecycleHookResult

    runner_module = _load_runner()
    events: list[str] = []

    def _spy_before(*_args: object, **_kwargs: object) -> LifecycleHookResult:
        events.append("before")
        return LifecycleHookResult(invoked=True, timed_out=False)

    def _spy_after(*_args: object, **_kwargs: object) -> LifecycleHookResult:
        events.append("after")
        return LifecycleHookResult(invoked=True, timed_out=False)

    # Patch via module __dict__ to satisfy the test-file
    # type-ignore ban and the B010 lint rule while still
    # exercising the real runner seam.
    original_before = lifecycle_module.before_agent_refresh
    original_after = lifecycle_module.after_agent_refresh
    lifecycle_module.__dict__["before_agent_refresh"] = _spy_before
    lifecycle_module.__dict__["after_agent_refresh"] = _spy_after
    try:
        # Avoid the agent actually running.
        def _fake_execute_effect_with_optional_display(*_args: object, **_kwargs: object) -> object:
            events.append("invocation")
            return MagicMock()

        monkeypatch.setattr(
            runner_module,
            "execute_effect_with_optional_display",
            _fake_execute_effect_with_optional_display,
        )
        config = _build_config(tmp_path)
        bundle = _make_fake_bundle()
        workspace_scope = MagicMock(root=tmp_path, allowed_roots=[tmp_path])

        # Pre-workspace with an explore_index attribute.
        pre_workspace = MagicMock()
        pre_workspace.explore_index = MagicMock()

        from ralph.pipeline.effects.invoke_agent_effect import (
            InvokeAgentEffect,
        )

        agent_effect = InvokeAgentEffect(
            agent_name="claude",
            phase="complete",
            prompt_file="prompt.md",
            drain="development",
        )

        # DEVELOPMENT drain: must observe pre + invocation + post.
        runner_module._invoke_execute_effect_with_optional_display(
            agent_effect,
            config,
            workspace_scope,
            display=None,
            verbosity=Verbosity.NORMAL,
            state=PipelineState(phase="complete"),
            policy_bundle=bundle,
            pre_workspace=pre_workspace,
            pre_phase_role="execution",
            pre_phase_drain="development",
        )
        assert events.count("before") == 1
        assert events.count("invocation") == 1
        assert events.count("after") == 1
        assert events == ["before", "invocation", "after"]

        # PLANNING drain: must observe no refresh events.
        events.clear()
        runner_module._invoke_execute_effect_with_optional_display(
            agent_effect,
            config,
            workspace_scope,
            display=None,
            verbosity=Verbosity.NORMAL,
            state=PipelineState(phase="complete"),
            policy_bundle=bundle,
            pre_workspace=pre_workspace,
            pre_phase_role="planning",
            pre_phase_drain="planning",
        )
        assert events == ["invocation"], (
            f"planning drain should not trigger refresh; got {events!r}"
        )

        # REVIEW drain: must observe no refresh events.
        events.clear()
        runner_module._invoke_execute_effect_with_optional_display(
            agent_effect,
            config,
            workspace_scope,
            display=None,
            verbosity=Verbosity.NORMAL,
            state=PipelineState(phase="complete"),
            policy_bundle=bundle,
            pre_workspace=pre_workspace,
            pre_phase_role="review",
            pre_phase_drain="review",
        )
        assert events == ["invocation"], f"review drain should not trigger refresh; got {events!r}"

        # COMMIT drain: must observe no refresh events.
        events.clear()
        runner_module._invoke_execute_effect_with_optional_display(
            agent_effect,
            config,
            workspace_scope,
            display=None,
            verbosity=Verbosity.NORMAL,
            state=PipelineState(phase="complete"),
            policy_bundle=bundle,
            pre_workspace=pre_workspace,
            pre_phase_role="commit",
            pre_phase_drain="commit",
        )
        assert events == ["invocation"], f"commit drain should not trigger refresh; got {events!r}"
    finally:
        lifecycle_module.__dict__["before_agent_refresh"] = original_before
        lifecycle_module.__dict__["after_agent_refresh"] = original_after
