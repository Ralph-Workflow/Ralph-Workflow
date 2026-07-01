"""Integration tests verifying plumbing commands run on the ``PipelineCore`` surface.

These tests mirror the coverage in ``test_plumbing_shared_deps.py`` but pass a
modular ``PipelineCore`` + ``bridge_factory`` instead of a full ``PipelineDeps``
bundle, confirming the new shared composition surface works end-to-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps, build_minimal_pipeline_core
from ralph.pipeline.plumbing import commit_plumbing as commit_plumbing_module
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.policy.models import AgentsPolicy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.display.context import DisplayContext


pytestmark = pytest.mark.timeout_seconds(5)


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


class _FakeOutputPath(Path):
    """Path stand-in that avoids real filesystem I/O for the smoke output file."""

    def exists(self) -> bool:
        return True

    def unlink(self, missing_ok: bool = False) -> None:
        del missing_ok


def test_commit_plumbing_runs_on_pipeline_core(
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_commit_plumbing`` accepts a ``PipelineCore`` + ``bridge_factory`` and
    returns a ``CommitAgentResult``; the bridge factory receives the commit drain
    and session prefix.
    """
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
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
                "model_identity": kwargs.get("model_identity"),
            }
        )
        return bridge

    core = build_minimal_pipeline_core(
        UnifiedConfig(), display_context, model_identity=model_identity
    )

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, pipeline_deps, workspace_scope, kwargs
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
            return_value="feat: pipeline core commit",
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
            pipeline_core=core,
            bridge_factory=fake_bridge_factory,
        )

    assert result.message == "feat: pipeline core commit"
    assert len(bridge_calls) == 1
    assert bridge_calls[0]["drain"] == "commit"
    assert bridge_calls[0]["session_id_prefix"] == "commit"
    assert bridge_calls[0]["model_identity"] is model_identity
    assert bridge.shutdown.call_count == 1
    mock_delete.assert_called_once_with(workspace_root)
    mock_read.assert_called_once_with(workspace_root)


def test_smoke_plumbing_runs_on_pipeline_core(
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_smoke_plumbing`` accepts a ``PipelineCore`` + ``bridge_factory`` and
    returns a ``SmokeRunResult``; the bridge factory receives the development
    drain and smoke session prefix.
    """
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
                "model_identity": kwargs.get("model_identity"),
            }
        )
        del kwargs
        return bridge

    core = build_minimal_pipeline_core(UnifiedConfig(), display_context)

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, pipeline_deps, workspace_scope, kwargs
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
    monkeypatch.setattr(
        smoke_plumbing_module,
        "is_artifact_submitted",
        lambda *args: True,
    )

    output_file = _FakeOutputPath("/workspace/tmp/interactive-claude-smoke/todo-list.js")

    result = smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=workspace_root,
        agent_name="claude/haiku",
        prompt_file=Path("/workspace/PROMPT.md"),
        output_file=output_file,
        display_context=display_context,
        pipeline_core=core,
        bridge_factory=fake_bridge_factory,
    )

    assert len(bridge_calls) == 1
    assert bridge_calls[0]["drain"] == "development"
    assert bridge_calls[0]["session_id_prefix"] == "smoke"
    assert bridge.shutdown.call_count == 1
    assert result.artifact_submitted is True


def test_plumbing_routes_through_execute_agent_effect(
    monkeypatch: MonkeyPatch,
) -> None:
    """Both plumbing modules route their agent invocation through
    ``execute_agent_effect`` when running on ``PipelineCore``.
    """
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    commit_calls: list[tuple[object, ...]] = []
    smoke_calls: list[tuple[object, ...]] = []

    def fake_commit_execute(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del workspace_scope, kwargs
        commit_calls.append((effect, config, pipeline_deps))
        return PipelineEvent.AGENT_SUCCESS

    def fake_smoke_execute(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del workspace_scope, kwargs
        smoke_calls.append((effect, config, pipeline_deps))
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        commit_plumbing_module,
        "execute_agent_effect",
        fake_commit_execute,
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
    monkeypatch.setattr(
        smoke_plumbing_module,
        "execute_agent_effect",
        fake_smoke_execute,
    )

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

    commit_core = build_minimal_pipeline_core(UnifiedConfig(), display_context)
    with (
        patch.object(commit_module, "delete_commit_message_artifacts"),
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: routed",
        ),
        patch.object(
            commit_module,
            "write_commit_prompt_file",
            return_value="PROMPT.md",
        ),
    ):
        commit_plumbing_module.run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=workspace_root,
            chain_config=_make_commit_chain_config(),
            display_context=display_context,
            pipeline_core=commit_core,
            bridge_factory=bridge_factory,
        )

    assert len(commit_calls) == 1
    assert isinstance(commit_calls[0][2], PipelineDeps)

    smoke_core = build_minimal_pipeline_core(UnifiedConfig(), display_context)
    output_file = _FakeOutputPath("/workspace/tmp/interactive-claude-smoke/todo-list.js")
    smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=workspace_root,
        agent_name="claude/haiku",
        prompt_file=Path("/workspace/PROMPT.md"),
        output_file=output_file,
        display_context=display_context,
        pipeline_core=smoke_core,
        bridge_factory=bridge_factory,
    )

    assert len(smoke_calls) == 1


def test_commit_plumbing_propagates_session_id(
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_commit_plumbing`` returns the session id captured by the shared
    execution core so callers can resume a suspended commit attempt.
    """
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    core = build_minimal_pipeline_core(UnifiedConfig(), display_context)

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, pipeline_deps, workspace_scope
        set_session_id_cb = kwargs.get("set_session_id_cb")
        if callable(set_session_id_cb):
            set_session_id_cb("sess-captured")
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
        patch.object(commit_module, "delete_commit_message_artifacts"),
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: session captured",
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
            display_context=display_context,
            pipeline_core=core,
            bridge_factory=bridge_factory,
        )

    assert result.message == "feat: session captured"
    assert result.session_id == "sess-captured"


def test_commit_plumbing_propagates_last_error(
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_commit_plumbing`` returns the last error reported by the shared
    execution core so callers can inspect why a commit attempt failed.
    """
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    core = build_minimal_pipeline_core(UnifiedConfig(), display_context)
    captured_error = AgentInvocationError("claude", 1, "boom")

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, pipeline_deps, workspace_scope
        error_sink = kwargs.get("agent_invocation_error_sink")
        if callable(error_sink):
            error_sink(captured_error)
        return PipelineEvent.AGENT_FAILURE

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
        patch.object(commit_module, "delete_commit_message_artifacts"),
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="",
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
            display_context=display_context,
            pipeline_core=core,
            bridge_factory=bridge_factory,
        )

    assert result.last_error is captured_error
    assert result.message == ""


def test_commit_plumbing_shuts_down_bridge_on_execute_raise(
    monkeypatch: MonkeyPatch,
) -> None:
    """If the body inside ``with_bridge_lifetime`` raises, the bridge is still
    shut down exactly once.
    """
    workspace_root = Path("/workspace")
    display_context = _fake_display_context()
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    core = build_minimal_pipeline_core(UnifiedConfig(), display_context)

    class _BodyError(Exception):
        pass

    def fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect, config, pipeline_deps, workspace_scope, kwargs
        raise _BodyError("agent effect failed")

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
        patch.object(commit_module, "delete_commit_message_artifacts"),
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="",
        ),
        patch.object(
            commit_module,
            "write_commit_prompt_file",
            return_value="PROMPT.md",
        ),
        pytest.raises(_BodyError),
    ):
        commit_plumbing_module.run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=workspace_root,
            chain_config=_make_commit_chain_config(),
            display_context=display_context,
            pipeline_core=core,
            bridge_factory=bridge_factory,
        )

    assert bridge.shutdown.call_count == 1
