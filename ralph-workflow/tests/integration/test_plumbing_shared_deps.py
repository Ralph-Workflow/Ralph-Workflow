"""Integration test verifying plumbing commands use the shared PipelineDeps path.

Both ``commit_plumbing`` and ``smoke_plumbing`` accept an injectable
``PipelineDeps`` bundle and use ``pipeline_deps.bridge_factory`` to create
their session bridge. They also forward the same dependency bundle to the
shared ``execute_agent_effect`` execution core. These tests exercise both
plumbing commands with fake collaborators and confirm that no real bridge
or subprocess is started.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.plumbing import commit_plumbing as commit_plumbing_module
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.policy.models import AgentsPolicy
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
        del effect, workspace_scope, kwargs
        captured_execute_calls.append({"config": config, "pipeline_deps": pipeline_deps})
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


def test_smoke_plumbing_uses_shared_pipeline_deps_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``run_smoke_plumbing`` creates the bridge through the injected
    ``PipelineDeps.bridge_factory`` and forwards the same bundle to
    ``execute_agent_effect``.
    """
    workspace_root = tmp_path
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
        del effect, workspace_scope, kwargs
        captured_execute_calls.append({"config": config, "pipeline_deps": pipeline_deps})
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
        "invoke_agent",
        _fake_smoke_invoke_agent,
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

    output_file = tmp_path / "tmp" / "interactive-claude-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("export const todos = [];\n", encoding="utf-8")

    result = smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=workspace_root,
        agent_name="claude/haiku",
        prompt_file=Path("PROMPT.md"),
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


def _fake_smoke_invoke_agent(
    _config: AgentConfig,
    _prompt_file: str,
    *,
    options: object = None,
) -> object:
    del options
    return iter(
        [
            '{"type":"assistant","message":{"type":"message","content":'
            '[{"type":"text","text":"I am creating the todo list now."}]}}\n',
            '{"type":"assistant","message":{"type":"message","content":'
            '[{"type":"text","text":"The file has been written successfully."}]}}\n',
            "Claude session ready. Session ID: interactive-smoke-session\n",
            "claude tool: write_file\n",
            "Task declared complete: session_id=interactive-smoke-session, "
            "summary=done, timestamp=1\n",
        ]
    )
