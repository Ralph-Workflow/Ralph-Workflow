"""Integration test verifying plumbing commands use the shared PipelineDeps path.

Both ``commit_plumbing`` and ``smoke_plumbing`` accept an injectable
``PipelineDeps`` bundle and use ``pipeline_deps.bridge_factory`` to create
their session bridge. They also forward the same dependency bundle to the
shared ``execute_agent_effect`` execution core. These tests exercise both
plumbing commands with fake collaborators and confirm that no real bridge
or subprocess is started.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.plumbing import commit_plumbing as commit_plumbing_module
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.pipeline.session_bridge import build_session_bridge
from ralph.pipeline.work_unit import WorkUnit
from ralph.policy.models import AgentsPolicy
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.display.context import DisplayContext


def _fake_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


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
    )


def _fake_commit_prompt(_diff: str, **kwargs: object) -> str:
    del _diff, kwargs
    return "commit prompt"


def _fake_commit_prompt_for_opencode(
    _diff: str,
    *,
    _submit_artifact_tool_name: str,
    **kwargs: object,
) -> str:
    del _diff, _submit_artifact_tool_name, kwargs
    return "commit prompt"


def _exercise_fake_executor_seam(agent_name: str) -> None:
    """Run a seeded ``FakeAgentExecutor`` to prove the fake agent boundary
    is available in a plumbing integration test.
    """
    unit = WorkUnit(
        unit_id=agent_name,
        description="plumbing integration work",
        allowed_directories=["tmp"],
    )
    executor = FakeAgentExecutor(
        runs={
            agent_name: FakeRun(
                outputs=["fake agent output"],
                exit_code=0,
                duration_ms=1,
            )
        }
    )
    asyncio.run(
        executor.run(
            unit,
            on_output=lambda _line: None,
            on_status=lambda _status: None,
        )
    )


def test_commit_plumbing_uses_shared_pipeline_deps_path(
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_commit_plumbing`` builds deps via the shared factory and forwards
    the same bundle to ``execute_agent_effect`` through
    ``PipelineDeps.bridge_factory``.
    """
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    bridge = MagicMock()
    bridge_factory_mock = MagicMock(return_value=bridge)
    shared_deps = PipelineDeps(
        display_context=display_context,
        bridge_factory=bridge_factory_mock,
    )
    captured_build_calls: list[tuple[object, ...]] = []
    captured_execute_calls: list[dict[str, object]] = []

    def fake_build_default_pipeline_deps(
        config: UnifiedConfig,
        ctx: DisplayContext,
        *,
        pro_hooks: object = None,
    ) -> PipelineDeps:
        captured_build_calls.append((config, ctx, pro_hooks))
        return shared_deps

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del workspace_scope, kwargs
        captured_execute_calls.append({"config": config, "pipeline_deps": pipeline_deps})
        _exercise_fake_executor_seam("commit-agent")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        commit_plumbing_module,
        "build_default_pipeline_deps",
        fake_build_default_pipeline_deps,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "execute_agent_effect",
        fake_execute_agent_effect,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message",
        _fake_commit_prompt,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message_for_opencode",
        _fake_commit_prompt_for_opencode,
    )

    with (
        patch.object(commit_module, "delete_commit_message_artifacts") as mock_delete,
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: shared deps commit",
        ) as mock_read,
        patch.object(
            commit_module,
            "write_commit_prompt_file",
            return_value="PROMPT.md",
        ),
    ):
        result = commit_plumbing_module.run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=workspace_root,
            chain_config=_make_commit_chain_config(),
            display_context=display_context,
        )

    assert result.message == "feat: shared deps commit"
    assert len(captured_build_calls) == 1
    assert captured_build_calls[0][1] is display_context
    assert len(captured_execute_calls) == 1

    executed_deps = captured_execute_calls[0]["pipeline_deps"]
    assert isinstance(executed_deps, PipelineDeps)
    assert executed_deps.display_context is display_context
    assert executed_deps.bridge_factory is bridge_factory_mock
    assert callable(executed_deps.registry_factory)
    assert callable(executed_deps.artifact_requirements_resolver)

    bridge_factory_mock.assert_called_once_with(
        workspace_root=workspace_root,
        drain="commit",
        agents_policy=_make_commit_chain_config().agents_policy,
        session_id_prefix="commit",
        model_identity=None,
    )
    assert cast("MagicMock", bridge.shutdown).call_count == 1
    mock_delete.assert_called_once_with(workspace_root)
    mock_read.assert_called_once_with(workspace_root)


class _FakeOutputPath(Path):
    """Path stand-in that avoids real filesystem I/O for the smoke output file."""

    def exists(self) -> bool:
        return True

    def unlink(self, missing_ok: bool = False) -> None:
        del missing_ok


def test_commit_default_bridge_forwards_model_identity(
    monkeypatch: MonkeyPatch,
) -> None:
    """The default commit bridge wrapper forwards the injected ``model_identity``
    to ``start_commit_bridge`` so the commit plumbing path preserves model
    context the same way the main pipeline does.
    """
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")

    class FakeBridge:
        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            return None

    captured_calls: list[dict[str, object]] = []

    def fake_start_commit_bridge(
        repo_root: Path,
        *,
        agents_policy: object = None,
        model_identity: object = None,
    ) -> FakeBridge:
        captured_calls.append(
            {
                "repo_root": repo_root,
                "agents_policy": agents_policy,
                "model_identity": model_identity,
            }
        )
        return FakeBridge()

    monkeypatch.setattr(
        commit_module,
        "start_commit_bridge",
        fake_start_commit_bridge,
    )

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del workspace_scope, kwargs
        _exercise_fake_executor_seam("commit-agent")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        commit_plumbing_module,
        "execute_agent_effect",
        fake_execute_agent_effect,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message",
        _fake_commit_prompt,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message_for_opencode",
        _fake_commit_prompt_for_opencode,
    )

    shared_deps = PipelineDeps(
        display_context=display_context,
        bridge_factory=build_session_bridge,
        model_identity=model_identity,
    )

    with (
        patch.object(commit_module, "delete_commit_message_artifacts") as mock_delete,
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: model identity preserved",
        ) as mock_read,
        patch.object(
            commit_module,
            "write_commit_prompt_file",
            return_value="PROMPT.md",
        ),
    ):
        result = commit_plumbing_module.run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=workspace_root,
            chain_config=_make_commit_chain_config(),
            display_context=display_context,
            pipeline_deps=shared_deps,
        )

    assert result.message == "feat: model identity preserved"
    assert len(captured_calls) == 1
    assert captured_calls[0]["repo_root"] == workspace_root
    assert captured_calls[0]["model_identity"] is model_identity
    mock_delete.assert_called_once_with(workspace_root)
    mock_read.assert_called_once_with(workspace_root)


def test_smoke_plumbing_uses_shared_pipeline_deps_path(
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_smoke_plumbing`` creates the bridge through the injected
    ``PipelineDeps.bridge_factory`` and forwards the same bundle to
    ``execute_agent_effect``.
    """
    workspace = MemoryWorkspace()
    workspace.write("PROMPT.md", "smoke prompt")
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    bridge = MagicMock()
    bridge_calls: list[dict[str, object]] = []

    def fake_bridge_factory(
        *,
        workspace_root: Path,
        drain: str,
        agents_policy: AgentsPolicy | None,
        session_id_prefix: str | None = None,
        **kwargs: object,
    ) -> MagicMock:
        bridge_calls.append(
            {
                "workspace_root": workspace_root,
                "drain": drain,
                "agents_policy": agents_policy,
                "session_id_prefix": session_id_prefix,
            }
        )
        del kwargs
        return bridge

    shared_deps = PipelineDeps(
        display_context=display_context,
        bridge_factory=fake_bridge_factory,
    )
    captured_execute_calls: list[dict[str, object]] = []

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del workspace_scope, kwargs
        captured_execute_calls.append({"config": config, "pipeline_deps": pipeline_deps})
        _exercise_fake_executor_seam("claude-haiku")
        return PipelineEvent.AGENT_SUCCESS

    def fake_read_smoke_result(_root: Path) -> dict[str, str]:
        return {"status": "passed", "summary": "ok"}

    def fake_resolve_workspace_scope(_start: Path | None) -> WorkspaceScope:
        return WorkspaceScope(workspace_root)

    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_smoke_registry(),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "execute_agent_effect",
        fake_execute_agent_effect,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "read_smoke_test_result_artifact",
        fake_read_smoke_result,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "resolve_workspace_scope",
        fake_resolve_workspace_scope,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "_clear_smoke_artifact",
        lambda _root: None,
    )

    output_file = _FakeOutputPath(
        "/workspace/tmp/interactive-claude-smoke/todo-list.js"
    )

    result = smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=workspace_root,
        agent_name="claude/haiku",
        prompt_file=Path(workspace.absolute_path("PROMPT.md")),
        output_file=output_file,
        display_context=display_context,
        pipeline_deps=shared_deps,
    )

    assert len(bridge_calls) == 1
    assert bridge_calls[0]["drain"] == "development"
    assert bridge_calls[0]["session_id_prefix"] == "smoke"
    assert cast("MagicMock", bridge.shutdown).call_count == 1
    assert len(captured_execute_calls) == 1
    assert captured_execute_calls[0]["pipeline_deps"] is shared_deps
    assert result.artifact_submitted is True


def test_commit_plumbing_resolves_display_context_from_pipeline_deps(
    monkeypatch: MonkeyPatch,
) -> None:
    """When no separate ``display_context`` is supplied, commit plumbing falls
    back to the one inside the injected ``PipelineDeps`` bundle, and it does
    not discard an injected ``artifact_requirements_resolver``.
    """
    workspace_root = Path("/workspace")
    deps_display_context = _fake_display_context()
    custom_resolver = MagicMock(return_value=None)
    bridge = MagicMock()
    bridge_factory_mock = MagicMock(return_value=bridge)
    shared_deps = PipelineDeps(
        display_context=deps_display_context,
        bridge_factory=bridge_factory_mock,
        artifact_requirements_resolver=custom_resolver,
    )
    captured_execute_calls: list[dict[str, object]] = []

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, workspace_scope
        captured_execute_calls.append(
            {"pipeline_deps": pipeline_deps, "display_context": kwargs.get("display_context")}
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        commit_plumbing_module,
        "execute_agent_effect",
        fake_execute_agent_effect,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message",
        _fake_commit_prompt,
    )
    monkeypatch.setattr(
        commit_plumbing_module,
        "prompt_commit_message_for_opencode",
        _fake_commit_prompt_for_opencode,
    )

    with (
        patch.object(commit_module, "delete_commit_message_artifacts") as mock_delete,
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: deps display",
        ),
        patch.object(
            commit_module,
            "write_commit_prompt_file",
            return_value="PROMPT.md",
        ),
    ):
        result = commit_plumbing_module.run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=workspace_root,
            chain_config=_make_commit_chain_config(),
            pipeline_deps=shared_deps,
        )

    assert result.message == "feat: deps display"
    assert len(captured_execute_calls) == 1
    executed_deps = captured_execute_calls[0]["pipeline_deps"]
    assert isinstance(executed_deps, PipelineDeps)
    assert executed_deps.display_context is deps_display_context
    assert executed_deps.artifact_requirements_resolver is custom_resolver
    assert captured_execute_calls[0]["display_context"] is deps_display_context
    mock_delete.assert_called_once_with(workspace_root)


def test_smoke_plumbing_resolves_display_context_from_pipeline_deps(
    monkeypatch: MonkeyPatch,
) -> None:
    """When no separate ``display_context`` is supplied, smoke plumbing falls
    back to the one inside the injected ``PipelineDeps`` bundle.
    """
    workspace = MemoryWorkspace()
    workspace.write("PROMPT.md", "smoke prompt")
    workspace_root = Path("/workspace")
    deps_display_context = _fake_display_context()
    bridge = MagicMock()
    bridge_calls: list[dict[str, object]] = []

    def fake_bridge_factory(
        *,
        workspace_root: Path,
        drain: str,
        agents_policy: AgentsPolicy | None,
        session_id_prefix: str | None = None,
        **kwargs: object,
    ) -> MagicMock:
        bridge_calls.append(
            {
                "workspace_root": workspace_root,
                "drain": drain,
                "agents_policy": agents_policy,
                "session_id_prefix": session_id_prefix,
            }
        )
        del kwargs
        return bridge

    shared_deps = PipelineDeps(
        display_context=deps_display_context,
        bridge_factory=fake_bridge_factory,
    )
    captured_execute_calls: list[dict[str, object]] = []

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, workspace_scope
        captured_execute_calls.append(
            {"pipeline_deps": pipeline_deps, "display_context": kwargs.get("display_context")}
        )
        return PipelineEvent.AGENT_SUCCESS

    def fake_read_smoke_result(_root: Path) -> dict[str, str]:
        return {"status": "passed", "summary": "ok"}

    def fake_resolve_workspace_scope(_start: Path | None) -> WorkspaceScope:
        return WorkspaceScope(workspace_root)

    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_smoke_registry(),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "execute_agent_effect",
        fake_execute_agent_effect,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "read_smoke_test_result_artifact",
        fake_read_smoke_result,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "resolve_workspace_scope",
        fake_resolve_workspace_scope,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "_clear_smoke_artifact",
        lambda _root: None,
    )

    output_file = _FakeOutputPath(
        "/workspace/tmp/interactive-claude-smoke/todo-list.js"
    )

    result = smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=workspace_root,
        agent_name="claude/haiku",
        prompt_file=Path(workspace.absolute_path("PROMPT.md")),
        output_file=output_file,
        pipeline_deps=shared_deps,
    )

    assert len(bridge_calls) == 1
    assert bridge_calls[0]["drain"] == "development"
    assert bridge_calls[0]["session_id_prefix"] == "smoke"
    assert cast("MagicMock", bridge.shutdown).call_count == 1
    assert len(captured_execute_calls) == 1
    executed_deps = captured_execute_calls[0]["pipeline_deps"]
    assert isinstance(executed_deps, PipelineDeps)
    assert executed_deps.display_context is deps_display_context
    assert captured_execute_calls[0]["display_context"] is deps_display_context
    assert result.artifact_submitted is True


def _make_fake_smoke_registry() -> object:
    interactive = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "claude/haiku":
                return interactive
            return None

    return FakeRegistry
