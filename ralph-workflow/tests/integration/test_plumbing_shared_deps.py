"""Integration test verifying plumbing commands use the shared ``PipelineDeps`` path.

Both ``commit_plumbing`` and ``smoke_plumbing`` build their dependency bundle
via ``build_default_pipeline_deps`` and delegate agent execution to
``execute_agent_effect``. This test patches those shared helpers to confirm
that the same ``PipelineDeps`` instance flows from composition through
execution without plumbing constructing its own collaborators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
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
from ralph.policy.models import AgentsPolicy

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

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


def test_commit_plumbing_uses_shared_pipeline_deps_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_commit_plumbing`` builds deps via the shared factory and forwards
    the same instance to ``execute_agent_effect``.
    """
    display_context = _fake_display_context()
    shared_deps = PipelineDeps(
        display_context=display_context,
        bridge_factory=MagicMock(),
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
        del effect, workspace_scope
        captured_execute_calls.append(
            {"config": config, "pipeline_deps": pipeline_deps, "kwargs": kwargs}
        )
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

    with (
        patch.object(commit_module, "delete_commit_message_artifacts") as mock_delete,
        patch.object(
            commit_module,
            "read_commit_message_artifact",
            return_value="feat: shared deps commit",
        ) as mock_read,
    ):
        result = commit_plumbing_module.run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=tmp_path,
            chain_config=_make_commit_chain_config(),
            display_context=display_context,
        )

    assert result.message == "feat: shared deps commit"
    assert len(captured_build_calls) == 1
    assert captured_build_calls[0][1] is display_context
    assert len(captured_execute_calls) == 1

    executed_deps = captured_execute_calls[0]["pipeline_deps"]
    assert isinstance(executed_deps, PipelineDeps)
    # ``_commit_pipeline_deps`` derives the execution deps from the bundle
    # returned by ``build_default_pipeline_deps``, preserving the injected
    # display context and bridge factory while overriding registry and
    # artifact-resolution collaborators for the commit path.
    assert executed_deps.display_context is display_context
    assert executed_deps.bridge_factory is shared_deps.bridge_factory
    assert callable(executed_deps.registry_factory)
    assert callable(executed_deps.artifact_requirements_resolver)
    mock_delete.assert_called_once_with(tmp_path)
    mock_read.assert_called_once_with(tmp_path)
