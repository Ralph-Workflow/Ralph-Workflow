"""Integration tests proving plumbing commands route through DefaultPipelineFactory.

These tests are black-box: they monkeypatch the factory and the plumbing dispatch,
invoke the CLI functions, and assert the factory received the expected kwargs and
that the full ``PipelineDeps`` surface (including the 8 extended Pro DI seams)
reaches plumbing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import click
import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands import smoke as smoke_module
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.plumbing.commit_plumbing import CommitAgentResult
from ralph.policy.models import AgentsPolicy
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.policy.models import PolicyBundle


def _make_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


class _RecordingPipelineFactory:
    """Conforms to ``PipelineFactory`` and records every build call."""

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


def test_commit_cli_uses_default_pipeline_factory(tmp_path: Path) -> None:
    display_context = _make_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
    pro_hooks = ProPipelineHooks(snapshot_registry=SnapshotRegistry())
    expected_deps = make_test_pipeline_deps(display_context)
    recording_factory = _RecordingPipelineFactory(expected_deps)
    captured_plumbing: dict[str, object] = {}

    def fake_run_commit_plumbing(**kwargs: object) -> CommitAgentResult:
        captured_plumbing["kwargs"] = kwargs
        return CommitAgentResult(message="feat: factory routed")

    chain_config = CommitChainConfig(
        registry=AgentRegistry(),
        agents=["claude"],
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=UnifiedConfig(),
    )

    with (
        patch.object(
            commit_module,
            "DefaultPipelineFactory",
            lambda *_args, **_kwargs: recording_factory,
        ),
        patch.object(commit_module, "run_commit_plumbing", fake_run_commit_plumbing),
    ):
        result = commit_module._generate_commit_message_with_chain(
            diff="diff --git a/x b/x",
            repo_root=tmp_path,
            chain_config=chain_config,
            display_context=display_context,
            model_identity=model_identity,
            pro_hooks=pro_hooks,
        )

    assert result.message == "feat: factory routed"
    assert len(recording_factory.calls) == 1
    factory_call = recording_factory.calls[0]
    assert factory_call["config"] is chain_config.general_config
    assert factory_call["display_context"] is display_context
    assert factory_call["model_identity"] is model_identity
    assert factory_call["pro_hooks"] is pro_hooks
    plumbing_kwargs = cast("dict[str, object]", captured_plumbing["kwargs"])
    assert plumbing_kwargs["pipeline_deps"] is expected_deps


def test_smoke_cli_uses_default_pipeline_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    display_context = _make_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="haiku")
    pro_hooks = ProPipelineHooks(snapshot_registry=SnapshotRegistry())
    expected_deps = make_test_pipeline_deps(display_context)
    recording_factory = _RecordingPipelineFactory(expected_deps)
    captured_plumbing: dict[str, object] = {}

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

    def fake_run_smoke_plumbing(**kwargs: object) -> smoke_module.SmokeRunResult:
        captured_plumbing["kwargs"] = kwargs
        return smoke_module.SmokeRunResult(
            agent_name="claude/haiku",
            transport="claude_interactive",
            output_file=tmp_path / "tmp" / "interactive-claude-smoke" / "todo-list.js",
            file_created=True,
            session_id="sess-1",
            explicit_completion_seen=True,
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=True,
            artifact_submitted=True,
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(
        smoke_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: recording_factory,
    )
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())
    monkeypatch.setattr(
        smoke_module,
        "submit_artifact_tool_name_for_transport",
        lambda _transport: "mcp__ralph__ralph_submit_artifact",
    )
    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)
    subagent_prompt_file = tmp_path / "subagent-prompt.txt"
    subagent_prompt_file.write_text(
        "Inspect watchdog evidence and report one likely stall edge case.",
        encoding="utf-8",
    )

    exit_code = smoke_module.smoke_interactive_claude_command(
        display_context=display_context,
        model_identity=model_identity,
        pro_hooks=pro_hooks,
        subagents=True,
        subagent_prompt_file=subagent_prompt_file,
    )

    assert exit_code == 0
    assert len(recording_factory.calls) == 1
    factory_call = recording_factory.calls[0]
    assert factory_call["display_context"] is display_context
    assert factory_call["model_identity"] is model_identity
    assert factory_call["pro_hooks"] is pro_hooks
    plumbing_kwargs = cast("dict[str, object]", captured_plumbing["kwargs"])
    assert plumbing_kwargs["pipeline_deps"] is expected_deps
    assert plumbing_kwargs["subagents"] is True
    prompt_path = cast("Path", plumbing_kwargs["prompt_file"])
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "Inspect watchdog evidence and report one likely stall edge case." in prompt
    assert "smoke_test_result" in prompt
    assert "declare_complete" in prompt


def test_subagent_prompt_file_requires_subagent_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        smoke_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path)
    )
    prompt_file = tmp_path / "subagent-prompt.txt"
    prompt_file.write_text("Inspect the parser.", encoding="utf-8")

    with pytest.raises(click.UsageError, match="requires --subagents"):
        smoke_module.smoke_harness_agent_command(
            "claude/haiku",
            subagent_prompt_file=prompt_file,
        )


def test_subagent_prompt_file_must_not_be_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        smoke_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path)
    )
    prompt_file = tmp_path / "subagent-prompt.txt"
    prompt_file.write_text("  \n", encoding="utf-8")

    with pytest.raises(click.UsageError, match="must not be empty"):
        smoke_module.smoke_harness_agent_command(
            "claude/haiku",
            subagents=True,
            subagent_prompt_file=prompt_file,
        )


def test_subagent_prompt_file_must_be_valid_utf8(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        smoke_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path)
    )
    prompt_file = tmp_path / "subagent-prompt.txt"
    prompt_file.write_bytes(b"\xff\xfe")

    with pytest.raises(click.UsageError, match="valid UTF-8"):
        smoke_module.smoke_harness_agent_command(
            "claude/haiku",
            subagents=True,
            subagent_prompt_file=prompt_file,
        )


def test_subagent_prompt_file_must_be_inside_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt_file = tmp_path / "outside-prompt.txt"
    prompt_file.write_text("Inspect the parser.", encoding="utf-8")
    monkeypatch.setattr(
        smoke_module, "resolve_workspace_scope", lambda: WorkspaceScope(workspace)
    )

    with pytest.raises(click.UsageError, match="must be inside the workspace"):
        smoke_module.smoke_harness_agent_command(
            "claude/haiku",
            subagents=True,
            subagent_prompt_file=prompt_file,
        )


def test_smoke_report_surfaces_ordered_subagent_evidence(tmp_path: Path) -> None:
    result = smoke_module.SmokeRunResult(
        agent_name="claude/haiku",
        transport="claude_interactive",
        output_file=tmp_path / "tmp" / "todo-list.js",
        file_created=True,
        session_id="sess-1",
        explicit_completion_seen=True,
        raw_line_count=4,
        parsed_event_count=4,
        tool_activity_seen=True,
        artifact_submitted=True,
        meaningful_output_lines=["tool_use: Agent", "tool_result: done"],
        errors=[],
        subagents_requested=True,
        subagent_dispatch_count=1,
        subagent_dispatch_seen=True,
        subagent_result_seen=True,
        post_subagent_activity_seen=True,
    )

    report = smoke_module.render_smoke_report([result])

    assert "subagent dispatch observed" in report
    assert "subagent result observed" in report
    assert "post-subagent activity observed" in report


def test_smoke_table_subagent_status_rejects_duplicate_dispatches(tmp_path: Path) -> None:
    result = smoke_module.SmokeRunResult(
        agent_name="claude/haiku",
        transport="claude_interactive",
        output_file=tmp_path / "tmp" / "todo-list.js",
        file_created=True,
        session_id="sess-1",
        explicit_completion_seen=True,
        raw_line_count=5,
        parsed_event_count=5,
        tool_activity_seen=True,
        artifact_submitted=True,
        meaningful_output_lines=["tool_use: Agent", "tool_result: done"],
        errors=["expected exactly one subagent dispatch, observed 2"],
        subagents_requested=True,
        subagent_dispatch_count=2,
        subagent_dispatch_seen=True,
        subagent_result_seen=True,
        post_subagent_activity_seen=True,
    )

    assert smoke_module._subagent_status(result) == "no"


def test_extended_pro_hooks_reach_plumbing_via_factory(tmp_path: Path) -> None:
    """All 8 extended Pro DI seams flow through the factory into plumbing."""
    display_context = _make_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
    bundle: PolicyBundle = cast("PolicyBundle", object())

    def my_policy_bundle_factory(_workspace_scope: object, _config: object) -> object:
        return bundle

    def my_registry_factory(_config: object) -> object:
        return object()

    def my_state_factory(
        _config: object,
        _agents_policy: object,
        _pipeline_policy: object,
        _budget_counters: object = None,
    ) -> object:
        return object()

    def my_recovery_controller_factory(
        _state: object,
        _policy_bundle: object,
        _config: object,
    ) -> tuple[object, int]:
        return object(), 0

    def my_marker_watcher_factory(_marker_path: object) -> object:
        return object()

    snapshot_registry = SnapshotRegistry()

    def my_recovery_sleep(_seconds: float) -> None:
        return None

    pro_hooks = ProPipelineHooks(
        policy_bundle_override=bundle,
        policy_bundle_factory=my_policy_bundle_factory,
        registry_factory=my_registry_factory,
        state_factory=my_state_factory,
        recovery_controller_factory=my_recovery_controller_factory,
        marker_watcher_factory=my_marker_watcher_factory,
        snapshot_registry=snapshot_registry,
        recovery_sleep=my_recovery_sleep,
    )
    captured_deps: list[PipelineDeps] = []

    def fake_run_commit_plumbing(**kwargs: object) -> CommitAgentResult:
        deps = kwargs.get("pipeline_deps")
        assert isinstance(deps, PipelineDeps)
        captured_deps.append(deps)
        return CommitAgentResult(message="feat: extended hooks reached plumbing")

    chain_config = CommitChainConfig(
        registry=AgentRegistry(),
        agents=["claude"],
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=UnifiedConfig(),
    )

    with patch.object(commit_module, "run_commit_plumbing", fake_run_commit_plumbing):
        result = commit_module._generate_commit_message_with_chain(
            diff="diff --git a/x b/x",
            repo_root=tmp_path,
            chain_config=chain_config,
            display_context=display_context,
            model_identity=model_identity,
            pro_hooks=pro_hooks,
        )

    assert result.message == "feat: extended hooks reached plumbing"
    assert len(captured_deps) == 1
    deps = captured_deps[0]
    assert deps.policy_bundle is bundle
    assert deps.policy_bundle_factory is my_policy_bundle_factory
    assert deps.registry_factory is my_registry_factory
    assert deps.state_factory is my_state_factory
    assert deps.recovery_controller_factory is my_recovery_controller_factory
    assert deps.marker_watcher_factory is my_marker_watcher_factory
    assert deps.snapshot_registry is snapshot_registry
    assert deps.recovery_sleep is my_recovery_sleep
    assert deps.model_identity is model_identity
