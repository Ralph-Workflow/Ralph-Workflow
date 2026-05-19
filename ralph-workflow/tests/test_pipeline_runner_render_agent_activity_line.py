"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console
from rich.text import Text

from ralph.agents.parsers import AgentOutputLine, ClaudeParser
from ralph.display.context import make_display_context
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    InvokeAgentEffect,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    PolicyBundle,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_TEXT_MAX = runner_module.MAX_TEXT_LENGTH + 1  # content + ellipsis
_TRUNCATED_RESULT_BRIEF_MAX = runner_module.MAX_TOOL_RESULT_BRIEF + 1  # content + ellipsis
_TRUNCATED_METADATA_MAX = runner_module.MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis
_AVAILABLE_WIDTH_FLOOR = 40
_TRUNCATE_RESULT_LEN = 6  # 5 chars + 1 ellipsis char


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_factory(return_value: object) -> object:
    class Registry:
        @classmethod
        def from_config(cls, config: object) -> object:
            instance = MagicMock()
            instance.get.return_value = return_value
            return instance

    return Registry


def _install_runner_display_context(
    monkeypatch: MonkeyPatch,
    *,
    width: int = 120,
) -> Console:
    console = Console(record=True, force_terminal=False, width=width, color_system=None)
    ctx = make_display_context(console=console, force_width=width, force_mode="wide")
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    return console


def _config_with_agents(
    *,
    agent_chains: dict[str, list[str]],
    agent_drains: dict[str, str],
) -> object:
    config = MagicMock()
    config.agent_chains = agent_chains
    config.agent_drains = agent_drains
    return config


def _write_minimal_plan_artifacts(
    root: Path,
    *,
    context: str = "Existing plan",
) -> None:
    (root / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "artifacts" / "plan.json").write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


def _write_minimal_plan_draft(root: Path, *, context: str = "Existing draft") -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan_draft.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:01+00:00",
                "sections": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


def test_resolve_display_defaults_to_legacy_console_display() -> None:
    display = runner_module.resolve_display(None, make_display_context())

    assert isinstance(display, runner_module.LegacyConsoleDisplay)


def test_materialize_agent_prompt_if_needed_rewrites_existing_prompt_on_fresh_planning_entry(
    tmp_path: Path,
) -> None:
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Create a fresh plan")
    workspace.write(
        ".agent/tmp/planning_prompt.md",
        "You are in PLANNING EDIT MODE. Revise the existing execution plan.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="planning",
        prompt_file="PROMPT.md",
        drain="planning",
        chain_name="planning",
    )
    state = PipelineState(phase="planning", previous_phase=None)
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/planning_prompt.md")
    assert "You are in PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered


def test_materialize_agent_prompt_if_needed_rewrites_stale_planning_prompt_on_analysis_loopback(
    tmp_path: Path,
) -> None:
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Revise the plan")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Existing plan",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "revise"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
    )
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.json",
        json.dumps(
            {
                "type": "planning_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Need revisions",
                    "what_came_up_short": ["issue"],
                    "how_to_fix": ["fix it"],
                },
            }
        ),
    )
    workspace.write(
        ".agent/tmp/planning_prompt.md",
        "You are in PLANNING MODE. Create a detailed, structured execution plan.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="planning",
        prompt_file="PROMPT.md",
        drain="planning",
        chain_name="planning",
    )
    state = PipelineState(phase="planning", previous_phase="planning_analysis")
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/planning_prompt.md")
    assert "PLANNING EDIT MODE" in rendered
    assert "You are in PLANNING MODE" not in rendered


@pytest.mark.parametrize("analysis_iteration", [2, 3, 4])
def test_materialize_agent_prompt_if_needed_rewrites_stale_development_prompt_on_analysis_loopback(
    tmp_path: Path,
    analysis_iteration: int,
) -> None:
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write(
        "PROMPT.md",
        f"Continue development after analysis iteration {analysis_iteration}",
    )
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\n1. Continue implementing the feature\n",
    )
    workspace.write(
        ".agent/tmp/development_prompt.md",
        "You are in IMPLEMENTATION MODE. Execute the plan and make progress.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file="PROMPT.md",
        drain="development",
        chain_name="development",
    )
    state = PipelineState(
        phase="development",
        previous_phase="development_analysis",
        loop_iterations={"development_analysis_iteration": analysis_iteration - 1},
        loop_caps={"development_analysis_iteration": 5},
    )
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/development_prompt.md")
    assert "continuing a DEVELOPMENT iteration" in rendered
    assert "You are in IMPLEMENTATION MODE" not in rendered


class TestRenderAgentActivityLine:
    def test_tool_use_includes_human_readable_input_summary(self) -> None:
        output = AgentOutputLine(
            type="tool_use",
            content="bash",
            metadata={
                "tool": "bash",
                "input": {
                    "command": "pytest -q",
                    "workdir": "/tmp/project",
                },
            },
        )

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "bash" in rendered.plain
        assert "command=pytest -q" in rendered.plain
        assert "workdir=/tmp/project" in rendered.plain
        assert "{" not in rendered.plain

    def test_non_text_event_summary_avoids_raw_json_dump(self) -> None:
        output = AgentOutputLine(
            type="item_plan_result",
            metadata={
                "status": "completed",
                "summary": "Plan submitted",
                "result": {"steps": 3},
            },
        )

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "status=completed" in rendered.plain
        assert "summary=Plan submitted" in rendered.plain
        assert "{" not in rendered.plain

    def test_tool_result_renders_content(self) -> None:
        output = AgentOutputLine(
            type="tool_result",
            content="{'matches': 3, 'path': 'src'}",
            metadata={
                "tool": "grep",
                "input": {"pattern": "TODO", "path": "src"},
                "result": {"matches": 3, "path": "src"},
            },
        )

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "result" in rendered.plain
        assert "{'matches': 3, 'path': 'src'}" in rendered.plain

    def test_claude_assistant_text_renders_without_extra_assistant_summary_line(self) -> None:
        parser = ClaudeParser()
        parsed = list(
            parser.parse(
                iter(
                    [
                        (
                            '{"type":"assistant","message":{"content":['
                            '{"type":"text","text":"Final response"}]}}'
                        )
                    ]
                )
            )
        )

        rendered = []
        for output in parsed:
            rendered_line = runner_module.render_agent_activity_line(output, "dev")
            if rendered_line is not None:
                rendered.append(rendered_line)

        assert [item.plain for item in rendered] == ["dev: Final response"]

    def test_tool_use_output_escapes_markup_like_input_before_console_render(self) -> None:
        output = AgentOutputLine(
            type="tool_use",
            content="Write",
            metadata={
                "input": {
                    "file_path": "/tmp/[unsafe].py",
                    "newText": "[/{color}]",
                }
            },
        )

        rendered = runner_module.render_agent_activity_line(output, "claude")

        assert rendered is not None

        console = Console(file=io.StringIO(), force_terminal=False, color_system=None)
        console.print(rendered)

    def test_analysis_prompt_session_drain_preserves_analysis_identity(self) -> None:
        assert (
            runner_module.prompt_session_drain_for_phase("development_analysis")
            is SessionDrain.DEVELOPMENT_ANALYSIS
        )
        assert (
            runner_module.prompt_session_drain_for_phase("review_analysis")
            is SessionDrain.REVIEW_ANALYSIS
        )

    def test_prompt_session_drain_uses_policy_drain_class_for_custom_analysis_phase(
        self,
    ) -> None:
        agents_policy = AgentsPolicy(
            agent_chains={"planning_analysis": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "planning_analysis": AgentDrainConfig(
                    chain="planning_analysis",
                    drain_class="analysis",
                )
            },
        )

        assert (
            runner_module.prompt_session_drain_for_phase(
                "planning_analysis", agents_policy=agents_policy
            )
            is SessionDrain.ANALYSIS
        )

    def test_text_truncation_for_long_content(self) -> None:
        long_content = "a" * 300
        output = AgentOutputLine(type="text", content=long_content)

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain
        content_part = rendered.plain.split(": ", 1)[1]
        assert len(content_part) <= _TRUNCATED_TEXT_MAX

    def test_tool_input_truncation(self) -> None:
        long_value = "x" * 200
        output = AgentOutputLine(
            type="tool_use",
            content="read_file",
            metadata={"input": {"path": long_value}},
        )

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain

    def test_error_format_with_symbol(self) -> None:
        output = AgentOutputLine(type="error", content="something broke")

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "✗" in rendered.plain
        assert "something broke" in rendered.plain

    def test_record_activity_uses_metadata_tool_for_tool_backed_errors(self) -> None:
        subscriber = MagicMock()
        parsed_line = AgentOutputLine(
            type="error",
            content="Git diff requires capability 'GitDiffRead': 'denied'",
            metadata={"tool": "git_diff"},
        )
        rendered = Text("opencode tool error: git_diff denied")

        runner_module.record_activity_on_subscriber(subscriber, parsed_line, rendered, "opencode")

        subscriber.record_activity.assert_called_once_with(
            unit_id="opencode",
            agent_name="opencode",
            line="opencode tool error: git_diff denied",
            tool_name="git_diff",
            path=None,
            workdir=None,
            command=None,
        )

    def test_tool_result_brief_for_very_long_content(self) -> None:
        long_result = "z" * 600
        output = AgentOutputLine(type="tool_result", content=long_result)

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain
        content_part = rendered.plain.split(": ", 1)[1]
        assert len(content_part) <= _TRUNCATED_RESULT_BRIEF_MAX

    def test_metadata_summary_caps_total_length(self) -> None:
        metadata: dict[str, object] = {
            "status": "a" * 50,
            "summary": "b" * 50,
            "phase": "c" * 50,
        }
        result = runner_module.metadata_summary(metadata)
        assert len(result) <= _TRUNCATED_METADATA_MAX
