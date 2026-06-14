"""Black-box tests for run_loop.run consuming PipelineDeps.

Uses injected fakes only: no real subprocess, no real network, no time.sleep,
no real file I/O.
"""

from __future__ import annotations

import importlib
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from rich.console import Console

if TYPE_CHECKING:
    import pytest

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
                json_path=".agent/artifacts/plan.json",
            )
        }
    )
    return PolicyBundle(pipeline=pipeline, agents=agents, artifacts=artifacts)


def _build_recovery_controller_mock() -> MagicMock:
    return MagicMock(
        spec=RecoveryController,
        event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
    )


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
