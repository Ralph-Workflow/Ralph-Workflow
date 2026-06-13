"""Tests for PipelineDeps / PipelineFactory.

Black-box tests using injected fakes only: no real subprocess, no real
network, no time.sleep, no real file I/O.
"""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.config.enums import Verbosity
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import RALPH_THEME
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.factory import (
    PipelineDeps,
    PipelineFactory,
    build_default_pipeline_deps,
)
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

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig


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


def _display_context() -> DisplayContext:
    return make_display_context(
        console=Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    )


class TestBuildDefaultPipelineDeps:
    """Tests for build_default_pipeline_deps."""

    def test_returns_frozen_deps_with_all_fields_populated(
        self, tmp_path: Path
    ) -> None:
        display_ctx = _display_context()
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
        )

        assert isinstance(deps, PipelineDeps)
        assert deps.display_context is display_ctx
        assert deps.model_identity is None
        assert deps.registry_factory is None
        assert deps.system_prompt_materializer is not None
        assert deps.phase_prompt_materializer is not None
        assert deps.artifact_requirements_resolver is not None
        assert deps.bridge_factory is not None
        assert deps.policy_bundle is None
        assert deps.policy_bundle_factory is None
        assert deps.state_factory is None
        assert deps.recovery_controller_factory is None
        assert deps.marker_watcher_factory is None
        assert deps.snapshot_registry is None

    def test_deps_is_frozen(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
        )

        def _assign(obj: object, attr: str, value: object) -> None:
            setattr(obj, attr, value)

        with pytest.raises(AttributeError):
            _assign(
                deps,
                "model_identity",
                MultimodalModelIdentity(provider="claude", model_id="sonnet"),
            )

    def test_accepts_pro_hooks_none(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=None,
        )

        assert deps.display_context is display_ctx


class TestPipelineDepsCollaborators:
    """Tests for per-field injection on PipelineDeps."""

    def test_bridge_factory_collaborator_is_overridable(self, tmp_path: Path) -> None:
        class FakeBridgeFactory:
            def __call__(
                self,
                *,
                workspace_root: Path,
                drain: str,
                agents_policy: object | None,
            ) -> object:
                return {"bridge": True}

        display_ctx = _display_context()
        fake = FakeBridgeFactory()
        deps = PipelineDeps(
            display_context=display_ctx,
            bridge_factory=fake,
        )

        assert deps.bridge_factory is fake
        result = deps.bridge_factory(workspace_root=tmp_path, drain="x", agents_policy=None)
        assert result == {"bridge": True}

    def test_model_identity_collaborator(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
        deps = PipelineDeps(
            display_context=display_ctx,
            model_identity=model_identity,
        )

        assert deps.model_identity == model_identity


class TestProHooksComposition:
    """Tests for ProPipelineHooks composition in build_default_pipeline_deps."""

    def test_policy_bundle_override_short_circuits(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        bundle = _make_fake_bundle()
        hooks = ProPipelineHooks(policy_bundle_override=bundle)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.policy_bundle is bundle
        assert deps.policy_bundle_factory is None

    def test_registry_factory_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_registry_factory = MagicMock(return_value=object())
        hooks = ProPipelineHooks(registry_factory=fake_registry_factory)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.registry_factory is fake_registry_factory

    def test_state_factory_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_state_factory = MagicMock(return_value=object())
        hooks = ProPipelineHooks(state_factory=fake_state_factory)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.state_factory is fake_state_factory

    def test_recovery_controller_factory_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_factory = MagicMock(return_value=(object(), 1))
        hooks = ProPipelineHooks(recovery_controller_factory=fake_factory)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.recovery_controller_factory is fake_factory

    def test_marker_watcher_factory_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_factory = MagicMock()
        hooks = ProPipelineHooks(marker_watcher_factory=fake_factory)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.marker_watcher_factory is fake_factory

    def test_snapshot_registry_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_registry = object()
        hooks = ProPipelineHooks(snapshot_registry=fake_registry)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.snapshot_registry is fake_registry

    def test_policy_bundle_factory_is_resolved_at_build_time(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        bundle = _make_fake_bundle()
        fake_factory = MagicMock(return_value=bundle)
        hooks = ProPipelineHooks(policy_bundle_factory=fake_factory)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.policy_bundle is bundle
        assert deps.policy_bundle_factory is None
        fake_factory.assert_called_once()

    def test_pro_factory_none_does_not_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        hooks = ProPipelineHooks()
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.policy_bundle is None
        assert deps.registry_factory is None


class TestPipelineFactoryProtocol:
    """Tests for PipelineFactory Protocol."""

    def test_runtime_checkable(self, tmp_path: Path) -> None:
        class FakeFactory:
            def build(
                self,
                config: UnifiedConfig,
                display_context: DisplayContext,
                *,
                pro_hooks: ProPipelineHooks | None = None,
            ) -> PipelineDeps:
                return build_default_pipeline_deps(config, display_context, pro_hooks=pro_hooks)

        factory: PipelineFactory = FakeFactory()
        assert isinstance(factory, PipelineFactory)
