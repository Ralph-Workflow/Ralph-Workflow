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
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import RALPH_THEME
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
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
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_recording_bridge_factory, make_test_pipeline_deps

if TYPE_CHECKING:
    from pathlib import Path


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
        assert deps.recovery_sleep is None

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

    def test_accepts_model_identity(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            model_identity=model_identity,
        )

        assert deps.model_identity is model_identity

    def test_accepts_policy_bundle(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        bundle = _make_fake_bundle()
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            policy_bundle=bundle,
        )

        assert deps.policy_bundle is bundle

    def test_pro_hooks_override_wins_over_policy_bundle_kwarg(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        bundle_from_kwarg = _make_fake_bundle()
        bundle_from_hooks = _make_fake_bundle()
        hooks = ProPipelineHooks(policy_bundle_override=bundle_from_hooks)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            policy_bundle=bundle_from_kwarg,
            pro_hooks=hooks,
        )

        assert deps.policy_bundle is bundle_from_hooks


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

    def test_recovery_sleep_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()

        def recovery_sleep(_seconds: float) -> None:
            return None

        hooks = ProPipelineHooks(recovery_sleep=recovery_sleep)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.recovery_sleep is recovery_sleep

    def test_recovery_sleep_argument_wins_over_default(self, tmp_path: Path) -> None:
        display_ctx = _display_context()

        def recovery_sleep(_seconds: float) -> None:
            return None

        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            recovery_sleep=recovery_sleep,
        )

        assert deps.recovery_sleep is recovery_sleep

    def test_pro_hooks_recovery_sleep_wins_over_argument(self, tmp_path: Path) -> None:
        display_ctx = _display_context()

        def arg_sleep(_seconds: float) -> None:
            return None

        def hook_sleep(_seconds: float) -> None:
            return None

        hooks = ProPipelineHooks(recovery_sleep=hook_sleep)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            recovery_sleep=arg_sleep,
            pro_hooks=hooks,
        )

        assert deps.recovery_sleep is hook_sleep

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

    def test_display_context_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        override_ctx = _display_context()
        hooks = ProPipelineHooks(display_context=override_ctx)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.display_context is override_ctx

    def test_model_identity_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
        hooks = ProPipelineHooks(model_identity=model_identity)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.model_identity is model_identity

    def test_system_prompt_materializer_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_materializer = MagicMock(return_value="fake-system-prompt.md")
        hooks = ProPipelineHooks(system_prompt_materializer=fake_materializer)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.system_prompt_materializer is fake_materializer

    def test_phase_prompt_materializer_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_materializer = MagicMock(return_value="fake-phase-prompt.md")
        hooks = ProPipelineHooks(phase_prompt_materializer=fake_materializer)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.phase_prompt_materializer is fake_materializer

    def test_artifact_requirements_resolver_override(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        fake_resolver = MagicMock(return_value=None)
        hooks = ProPipelineHooks(artifact_requirements_resolver=fake_resolver)
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.artifact_requirements_resolver is fake_resolver

    def test_all_collaborator_overrides_applied_together(self, tmp_path: Path) -> None:
        display_ctx = _display_context()
        override_ctx = _display_context()
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
        fake_system_materializer = MagicMock(return_value="fake-system-prompt.md")
        fake_phase_materializer = MagicMock(return_value="fake-phase-prompt.md")
        fake_resolver = MagicMock(return_value=None)
        hooks = ProPipelineHooks(
            display_context=override_ctx,
            model_identity=model_identity,
            system_prompt_materializer=fake_system_materializer,
            phase_prompt_materializer=fake_phase_materializer,
            artifact_requirements_resolver=fake_resolver,
        )
        deps = build_default_pipeline_deps(
            _build_config(tmp_path),
            display_ctx,
            pro_hooks=hooks,
        )

        assert deps.display_context is override_ctx
        assert deps.model_identity is model_identity
        assert deps.system_prompt_materializer is fake_system_materializer
        assert deps.phase_prompt_materializer is fake_phase_materializer
        assert deps.artifact_requirements_resolver is fake_resolver


class TestPipelineSharedExecutionCore:
    """Regression tests proving PipelineDeps drives the shared execution core."""

    def test_execute_agent_effect_uses_pipeline_deps_bridge_factory(
        self,
        tmp_path: Path,
    ) -> None:
        display_context = _display_context()
        config = UnifiedConfig(
            agents={"dev": AgentConfig(cmd="opencode", output_flag="--json-stream")}
        )
        effect = InvokeAgentEffect(
            agent_name="dev", phase="development", prompt_file="dev.md"
        )
        factory = make_recording_bridge_factory()
        deps = make_test_pipeline_deps(
            display_context=display_context,
            bridge_factory=factory,
        )

        def fake_invoke_agent(
            _agent_config: AgentConfig,
            _prompt_file: str,
            *,
            options: object = None,
        ) -> list[str]:
            del _agent_config, _prompt_file, options
            return []

        event = execute_agent_effect(
            effect,
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=display_context,
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=RuntimeError,
        )

        assert event == PipelineEvent.AGENT_SUCCESS
        assert len(factory.calls) == 1
        assert factory.calls[0]["drain"] == "development"

    def test_execute_agent_effect_uses_pipeline_deps_artifact_requirements_resolver(
        self,
        tmp_path: Path,
    ) -> None:
        display_context = _display_context()
        config = UnifiedConfig(
            agents={"dev": AgentConfig(cmd="opencode", output_flag="--json-stream")}
        )
        effect = InvokeAgentEffect(
            agent_name="dev", phase="planning", prompt_file="plan.md", drain="planning"
        )
        bundle = _make_fake_bundle()
        resolver_calls: list[dict[str, object]] = []

        def recording_resolver(
            pipeline_policy: object,
            artifacts_policy: object,
            *,
            phase: str,
            drain: str | None = None,
        ) -> object:
            resolver_calls.append(
                {
                    "pipeline_policy": pipeline_policy,
                    "artifacts_policy": artifacts_policy,
                    "phase": phase,
                    "drain": drain,
                }
            )
            return None

        deps = make_test_pipeline_deps(
            display_context=display_context,
            artifact_requirements_resolver=recording_resolver,
            policy_bundle=bundle,
        )

        def fake_invoke_agent(
            _agent_config: AgentConfig,
            _prompt_file: str,
            *,
            options: object = None,
        ) -> list[str]:
            del _agent_config, _prompt_file, options
            return []

        event = execute_agent_effect(
            effect,
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=display_context,
            policy_bundle=bundle,
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=RuntimeError,
        )

        assert event == PipelineEvent.AGENT_SUCCESS
        assert len(resolver_calls) == 1
        assert resolver_calls[0]["phase"] == "planning"
        assert resolver_calls[0]["drain"] == "planning"
        assert resolver_calls[0]["pipeline_policy"] is bundle.pipeline
        assert resolver_calls[0]["artifacts_policy"] is bundle.artifacts


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
