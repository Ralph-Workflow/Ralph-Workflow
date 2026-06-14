"""Black-box tests for ``DefaultPipelineFactory``.

These tests pin the Protocol compliance, the production-default build, and the
propagation of all 13 ``ProPipelineHooks`` fields through the factory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineDeps, PipelineFactory
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _make_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


class TestDefaultPipelineFactoryProtocol:
    def test_isinstance_against_pipeline_factory_protocol(self) -> None:
        factory = DefaultPipelineFactory()

        assert isinstance(factory, PipelineFactory)


class TestDefaultPipelineFactoryBuild:
    def test_build_returns_pipeline_deps_with_production_defaults(self) -> None:
        config = UnifiedConfig()
        display_context = _make_display_context()

        deps = DefaultPipelineFactory().build(config, display_context)

        assert isinstance(deps, PipelineDeps)
        assert deps.display_context is display_context
        assert deps.model_identity is None
        assert deps.policy_bundle is None
        assert deps.policy_bundle_factory is None
        assert deps.registry_factory is None
        assert deps.state_factory is None
        assert deps.recovery_controller_factory is None
        assert deps.marker_watcher_factory is None
        assert deps.snapshot_registry is None
        assert deps.recovery_sleep is None

    def test_build_forwards_model_identity(self) -> None:
        config = UnifiedConfig()
        display_context = _make_display_context()
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")

        deps = DefaultPipelineFactory().build(
            config, display_context, model_identity=model_identity
        )

        assert deps.model_identity is model_identity


class TestDefaultPipelineFactoryAppliesProHooksToCore:
    def test_build_applies_five_modular_pro_collaborators(self) -> None:
        config = UnifiedConfig()
        override_ctx = _make_display_context()
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")

        def fake_system_prompt_materializer(
            workspace_root: object,
            name: object,
            default_current_prompt: object = None,
            worker_namespace: object = None,
        ) -> str:
            del workspace_root, name, default_current_prompt, worker_namespace
            return "system"

        def fake_phase_prompt_materializer(
            context: object = None,
            options: object = None,
            **kwargs: object,
        ) -> str:
            del context, options, kwargs
            return "phase"

        def fake_artifact_resolver(
            pipeline_policy: object,
            artifacts_policy: object,
            *,
            phase: object,
            drain: object = None,
        ) -> None:
            del pipeline_policy, artifacts_policy, phase, drain

        hooks = ProPipelineHooks(
            display_context=override_ctx,
            model_identity=model_identity,
            system_prompt_materializer=fake_system_prompt_materializer,
            phase_prompt_materializer=fake_phase_prompt_materializer,
            artifact_requirements_resolver=fake_artifact_resolver,
        )

        deps = DefaultPipelineFactory().build(config, _make_display_context(), pro_hooks=hooks)

        assert deps.display_context is override_ctx
        assert deps.model_identity is model_identity
        assert deps.system_prompt_materializer is fake_system_prompt_materializer
        assert deps.phase_prompt_materializer is fake_phase_prompt_materializer
        assert deps.artifact_requirements_resolver is fake_artifact_resolver


class TestDefaultPipelineFactoryAppliesProHooksToExtendedSurface:
    def test_build_propagates_all_eight_extended_pro_fields(self) -> None:
        config = UnifiedConfig()
        display_context = _make_display_context()
        bundle = cast("PolicyBundle", object())

        def policy_bundle_factory(_workspace_scope: object, _config: object) -> object:
            return bundle

        def registry_factory(_config: object) -> object:
            return object()

        def state_factory(
            _config: object,
            _agents_policy: object,
            _pipeline_policy: object,
            _budget_counters: object = None,
        ) -> object:
            return object()

        def recovery_controller_factory(
            _state: object,
            _policy_bundle: object,
            _config: object,
        ) -> tuple[object, int]:
            return object(), 0

        def marker_watcher_factory(_marker_path: object) -> object:
            return object()

        snapshot_registry = SnapshotRegistry()

        def recovery_sleep(_seconds: float) -> None:
            return None

        hooks = ProPipelineHooks(
            policy_bundle_override=bundle,
            policy_bundle_factory=policy_bundle_factory,
            registry_factory=registry_factory,
            state_factory=state_factory,
            recovery_controller_factory=recovery_controller_factory,
            marker_watcher_factory=marker_watcher_factory,
            snapshot_registry=snapshot_registry,
            recovery_sleep=recovery_sleep,
        )

        deps = DefaultPipelineFactory().build(config, display_context, pro_hooks=hooks)

        assert deps.policy_bundle is bundle
        assert deps.policy_bundle_factory is policy_bundle_factory
        assert deps.registry_factory is registry_factory
        assert deps.state_factory is state_factory
        assert deps.recovery_controller_factory is recovery_controller_factory
        assert deps.marker_watcher_factory is marker_watcher_factory
        assert deps.snapshot_registry is snapshot_registry
        assert deps.recovery_sleep is recovery_sleep
