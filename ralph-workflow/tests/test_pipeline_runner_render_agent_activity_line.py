"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import io
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
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    PolicyBundle,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_METADATA_MAX = runner_module.MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis
_AVAILABLE_WIDTH_FLOOR = 40
_TRUNCATE_RESULT_LEN = 6  # 5 chars + 1 ellipsis char


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _policy_bundle_with_loop_counter(counter_name: str, default_max: int) -> PolicyBundle:
    bundle = _load_default_policy_bundle()
    loop_counters = dict(bundle.pipeline.loop_counters)
    loop_counters[counter_name] = loop_counters[counter_name].model_copy(
        update={"default_max": default_max}
    )
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"loop_counters": loop_counters})}
    )


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
    ctx = make_display_context(
        console=console,
        force_width=width,
    )
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
    (root / ".agent" / "artifacts" / "plan.md").write_text(
        f"---\ntype: plan\n---\n## Work\n### [S-1] Preserve {context}\nInspect the existing plan handoff.\nType: discovery\nLocation: .agent/artifacts/plan.md\n",
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


def _write_minimal_plan_draft(root: Path, *, context: str = "Existing draft") -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan.draft.md").write_text(
        f"---\ntype: plan\n---\n## Work\n### [S-1] Preserve {context}\nInspect the existing plan handoff.\nType: discovery\nLocation: .agent/artifacts/plan.md\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


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
        # After wt-028-display the pipeline-runner path delegates to
        # the canonical registry; a successful TOOL_RESULT carries the
        # PASS carrier (✓ + "PASS" label) and the agent prefix. The
        # legacy "result <content>" prefix word is replaced by the
        # carrier label which carries the same semantic meaning
        # (success-state tool result) under the redundant label
        # contract (AC-10).
        assert "{'matches': 3, 'path': 'src'}" in rendered.plain
        assert "PASS" in rendered.plain or "✓" in rendered.plain

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

        # After wt-028-display the pipeline runner routes through the
        # agent-event renderer registry. Claude ``assistant`` text events
        # carry the registry's INFO carrier (icon + ``INFO`` label) plus
        # the agent_name prefix and the body. The body still surfaces
        # without a duplicate ``assistant`` summary line.
        assert len(rendered) == 1
        rendered_plain = rendered[0].plain
        assert "Final response" in rendered_plain
        assert "assistant" not in rendered_plain
        assert "dev" in rendered_plain

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

    def test_prompt_session_drain_prefers_target_phase_policy_over_stale_terminal_drain(
        self,
    ) -> None:
        bundle = _load_default_policy_bundle()

        assert (
            runner_module.prompt_session_drain_for_phase(
                "failed_terminal",
                phase="planning",
                pipeline_policy=bundle.pipeline,
                agents_policy=bundle.agents,
            )
            is SessionDrain.PLANNING
        )

    def test_prompt_session_drain_falls_back_to_terminal_role_profile_for_failed_terminal(
        self,
    ) -> None:
        bundle = _load_default_policy_bundle()

        assert (
            runner_module.prompt_session_drain_for_phase(
                "failed_terminal",
                phase="failed_terminal",
                pipeline_policy=bundle.pipeline,
                agents_policy=bundle.agents,
            )
            is SessionDrain.ANALYSIS
        )

    def test_text_truncation_for_long_content(self) -> None:
        long_content = "a" * 300
        output = AgentOutputLine(type="text", content=long_content)

        rendered = runner_module.render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain
        # Text rows retain their inline identity for compatibility; tool
        # results instead use the shared live chrome as their sole identity.
        assert "dev" in rendered.plain
        # Total plain length stays within the registry's 200-cell cap.
        assert len(rendered.plain) <= 250  # body + icon + label + agent prefix + ts

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
        # Tool-result identity belongs to the shared live chrome, not its body.
        assert "dev" not in rendered.plain
        assert len(rendered.plain) <= 250

    def test_metadata_summary_caps_total_length(self) -> None:
        metadata: dict[str, object] = {
            "status": "a" * 50,
            "summary": "b" * 50,
            "phase": "c" * 50,
        }
        result = runner_module.metadata_summary(metadata)
        assert len(result) <= _TRUNCATED_METADATA_MAX
