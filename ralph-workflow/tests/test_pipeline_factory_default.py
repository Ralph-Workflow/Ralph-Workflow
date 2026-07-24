"""Black-box tests for ``DefaultPipelineFactory``.

These tests pin the Protocol compliance, the production-default build, and the
propagation of all 13 ``ProPipelineHooks`` fields through the factory.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineDeps, PipelineFactory
from ralph.pipeline.plumbing import commit_plumbing as commit_plumbing_module
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.pipeline.plumbing.commit_plumbing import run_commit_plumbing
from ralph.pipeline.plumbing.smoke_plumbing import run_smoke_plumbing
from ralph.policy.models import AgentsPolicy
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.policy.models import PolicyBundle


def _make_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


class _RecordingPipelineFactory:
    """Conforms to ``PipelineFactory`` and records every ``build`` call."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps
        self.calls: list[dict[str, object]] = []

    def build(
        self,
        config: UnifiedConfig,
        display_context: DisplayContext,
        *,
        model_identity: MultimodalModelIdentity | None = None,
        pro_hooks: ProPipelineHooks | None = None,
        **kwargs: object,
    ) -> PipelineDeps:
        del kwargs
        self.calls.append(
            {
                "config": config,
                "display_context": display_context,
                "model_identity": model_identity,
                "pro_hooks": pro_hooks,
            }
        )
        return self._deps


def _make_commit_chain_config() -> CommitChainConfig:
    registry = AgentRegistry()
    registry.register(
        "claude-headless",
        AgentConfig(
            cmd="claude -p",
            output_flag="--output-format=stream-json",
            yolo_flag="--permission-mode auto",
            can_commit=True,
            json_parser=JsonParserType.CLAUDE,
            transport=AgentTransport.CLAUDE,
        ),
    )
    return CommitChainConfig(
        registry=registry,
        agents=["claude-headless"],
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=UnifiedConfig(),
    )


def _make_fake_smoke_registry() -> object:
    interactive = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "claude/haiku":
                return interactive
            return None

    return FakeRegistry


@contextmanager
def _fake_with_bridge_lifetime(*args: object, **kwargs: object) -> Iterator[MagicMock]:
    del args, kwargs
    yield MagicMock()


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

        def fake_master_prompt_materializer(
            workspace_root: object,
            name: object,
            default_product_criteria: object = None,
            worker_namespace: object = None,
        ) -> str:
            del workspace_root, name, default_product_criteria, worker_namespace
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
            master_prompt_materializer=fake_master_prompt_materializer,
            phase_prompt_materializer=fake_phase_prompt_materializer,
            artifact_requirements_resolver=fake_artifact_resolver,
        )

        deps = DefaultPipelineFactory().build(config, _make_display_context(), pro_hooks=hooks)

        assert deps.display_context is override_ctx
        assert deps.model_identity is model_identity
        assert deps.master_prompt_materializer is fake_master_prompt_materializer
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


def test_run_commit_plumbing_routes_fallback_through_default_pipeline_factory(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    display_context = _make_display_context()
    expected_deps = make_test_pipeline_deps(display_context)
    recording_factory = _RecordingPipelineFactory(expected_deps)
    captured_execute_calls: list[dict[str, object]] = []

    chain_config = _make_commit_chain_config()

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, workspace_scope, kwargs
        captured_execute_calls.append({"pipeline_deps": pipeline_deps})
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        commit_plumbing_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: recording_factory,
    )
    monkeypatch.setattr(commit_plumbing_module, "with_bridge_lifetime", _fake_with_bridge_lifetime)
    monkeypatch.setattr(commit_plumbing_module, "execute_agent_effect", fake_execute_agent_effect)
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message",
        lambda _diff, **kwargs: "prompt",
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message_for_opencode",
        lambda _diff, **kwargs: "prompt",
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "write_commit_prompt_file",
        lambda _repo_root, _prompt: str(tmp_path / "prompt.md"),
    )

    with (
        patch.object(commit_module, "delete_commit_message_artifacts"),
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: routed",
        ),
    ):
        result = run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=tmp_path,
            chain_config=chain_config,
            display_context=display_context,
        )

    assert result.message == "feat: routed"
    assert len(recording_factory.calls) == 1
    factory_call = recording_factory.calls[0]
    assert factory_call["config"] is chain_config.general_config
    assert factory_call["display_context"] is display_context
    assert len(captured_execute_calls) == 1
    executed_deps = captured_execute_calls[0]["pipeline_deps"]
    assert isinstance(executed_deps, PipelineDeps)
    assert executed_deps.core is expected_deps.core


def test_run_smoke_plumbing_routes_fallback_through_default_pipeline_factory(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    display_context = _make_display_context()
    expected_deps = make_test_pipeline_deps(display_context)
    recording_factory = _RecordingPipelineFactory(expected_deps)
    captured_execute_calls: list[dict[str, object]] = []

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, workspace_scope, kwargs
        captured_execute_calls.append({"pipeline_deps": pipeline_deps})
        return PipelineEvent.AGENT_SUCCESS

    output_file = MagicMock()
    output_file.exists.return_value = True

    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_smoke_registry(),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: recording_factory,
    )
    monkeypatch.setattr(smoke_plumbing_module, "with_bridge_lifetime", _fake_with_bridge_lifetime)
    monkeypatch.setattr(smoke_plumbing_module, "execute_agent_effect", fake_execute_agent_effect)
    monkeypatch.setattr(
        smoke_plumbing_module,
        "read_smoke_test_result_artifact",
        lambda _root: {"status": "passed", "summary": "ok"},
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "resolve_workspace_scope",
        lambda _start: WorkspaceScope(tmp_path),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "_clear_smoke_artifact",
        lambda _root: None,
    )

    config = UnifiedConfig()
    result = run_smoke_plumbing(
        config=config,
        workspace_root=tmp_path,
        agent_name="claude/haiku",
        prompt_file=tmp_path / "p.md",
        output_file=output_file,
        display_context=display_context,
    )

    assert result.agent_name == "claude/haiku"
    assert len(recording_factory.calls) == 1
    factory_call = recording_factory.calls[0]
    assert factory_call["config"] is config
    assert factory_call["display_context"] is display_context
    assert len(captured_execute_calls) == 1
    assert captured_execute_calls[0]["pipeline_deps"] is expected_deps


def test_run_commit_plumbing_pro_hooks_reach_factory(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    display_context = _make_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
    pro_hooks = ProPipelineHooks(
        snapshot_registry=SnapshotRegistry(),
        model_identity=model_identity,
    )
    expected_deps = make_test_pipeline_deps(display_context)
    recording_factory = _RecordingPipelineFactory(expected_deps)

    chain_config = _make_commit_chain_config()

    monkeypatch.setattr(
        commit_plumbing_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: recording_factory,
    )
    monkeypatch.setattr(commit_plumbing_module, "with_bridge_lifetime", _fake_with_bridge_lifetime)
    monkeypatch.setattr(
        commit_plumbing_module,
        "execute_agent_effect",
        lambda *_args, **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message",
        lambda _diff, **kwargs: "prompt",
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message_for_opencode",
        lambda _diff, **kwargs: "prompt",
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "write_commit_prompt_file",
        lambda _repo_root, _prompt: str(tmp_path / "prompt.md"),
    )

    with (
        patch.object(commit_module, "delete_commit_message_artifacts"),
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: hooks",
        ),
    ):
        run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=tmp_path,
            chain_config=chain_config,
            display_context=display_context,
            pro_hooks=pro_hooks,
        )

    assert len(recording_factory.calls) == 1
    assert recording_factory.calls[0]["pro_hooks"] is pro_hooks
    assert recording_factory.calls[0]["pro_hooks"].model_identity is model_identity


def test_run_smoke_plumbing_pro_hooks_reach_factory(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    display_context = _make_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
    pro_hooks = ProPipelineHooks(
        snapshot_registry=SnapshotRegistry(),
        model_identity=model_identity,
    )
    expected_deps = make_test_pipeline_deps(display_context)
    recording_factory = _RecordingPipelineFactory(expected_deps)

    output_file = MagicMock()
    output_file.exists.return_value = True

    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_smoke_registry(),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: recording_factory,
    )
    monkeypatch.setattr(smoke_plumbing_module, "with_bridge_lifetime", _fake_with_bridge_lifetime)
    monkeypatch.setattr(
        smoke_plumbing_module,
        "execute_agent_effect",
        lambda *_args, **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "read_smoke_test_result_artifact",
        lambda _root: {"status": "passed", "summary": "ok"},
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "resolve_workspace_scope",
        lambda _start: WorkspaceScope(tmp_path),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "_clear_smoke_artifact",
        lambda _root: None,
    )

    config = UnifiedConfig()
    run_smoke_plumbing(
        config=config,
        workspace_root=tmp_path,
        agent_name="claude/haiku",
        prompt_file=tmp_path / "p.md",
        output_file=output_file,
        display_context=display_context,
        pro_hooks=pro_hooks,
    )

    assert len(recording_factory.calls) == 1
    assert recording_factory.calls[0]["pro_hooks"] is pro_hooks
    assert recording_factory.calls[0]["pro_hooks"].model_identity is model_identity


def test_run_commit_plumbing_uses_hooked_display_context_at_dispatch_boundary(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    """Regression: the display_context forwarded to execute_agent_effect and
    collect_commit_agent_output must be the one produced by the factory, not
    the original caller display_context. ProPipelineHooks.display_context is
    applied by DefaultPipelineFactory, so the commit plumbing fallback path
    must rebind display_context from the returned PipelineDeps.
    """
    original_display_context = _make_display_context()
    hooked_display_context = _make_display_context()
    expected_deps = make_test_pipeline_deps(hooked_display_context)
    recording_factory = _RecordingPipelineFactory(expected_deps)
    captured_execute_display_context: object | None = None
    captured_collect_display_context: object | None = None
    chain_config = _make_commit_chain_config()

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        nonlocal captured_execute_display_context
        del effect, config, pipeline_deps, workspace_scope
        captured_execute_display_context = kwargs.get("display_context")
        return PipelineEvent.AGENT_SUCCESS

    def fake_collect_commit_agent_output(
        lines: object,
        *,
        parser_type: object,
        agent_name: object,
        verbose: object,
        display_context: object,
        session_id_sink: object | None = None,
    ) -> tuple[list[str], list[str], None]:
        del lines, parser_type, agent_name, verbose, session_id_sink
        nonlocal captured_collect_display_context
        captured_collect_display_context = display_context
        return [], [], None

    monkeypatch.setattr(
        commit_plumbing_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: recording_factory,
    )
    monkeypatch.setattr(commit_plumbing_module, "with_bridge_lifetime", _fake_with_bridge_lifetime)
    monkeypatch.setattr(commit_plumbing_module, "execute_agent_effect", fake_execute_agent_effect)
    monkeypatch.setattr(
        commit_plumbing_module,
        "collect_commit_agent_output",
        fake_collect_commit_agent_output,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message",
        lambda _diff, **kwargs: "prompt",
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message_for_opencode",
        lambda _diff, **kwargs: "prompt",
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "write_commit_prompt_file",
        lambda _repo_root, _prompt: str(tmp_path / "prompt.md"),
    )

    with (
        patch.object(commit_module, "delete_commit_message_artifacts"),
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: hooked display context",
        ),
    ):
        run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=tmp_path,
            chain_config=chain_config,
            display_context=original_display_context,
        )

    assert len(recording_factory.calls) == 1
    assert recording_factory.calls[0]["display_context"] is original_display_context
    assert captured_execute_display_context is hooked_display_context
    assert captured_collect_display_context is hooked_display_context
