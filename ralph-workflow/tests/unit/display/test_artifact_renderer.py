from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
    render_analysis_decision,
    render_commit_message,
    render_development_artifact,
    render_fix_artifact,
    render_plan_artifact,
    render_review_artifact,
)
from ralph.display.context import DisplayContext, make_display_context


def _make_console() -> Console:
    return Console(
        file=cast("StringIO", StringIO()),
        force_terminal=True,
        color_system=None,
        width=120,
    )


def _make_display_context() -> DisplayContext:
    console = _make_console()
    return make_display_context(console=console, env={})


def _console_output(console: Console) -> str:
    return cast("StringIO", console.file).getvalue()


class TestRenderPlanArtifact:
    def test_renders_plan_block_when_file_present(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "plan.json").write_text(
            json.dumps(
                {
                    "content": {
                        "summary": {
                            "context": "Test plan",
                            "scope_items": ["item1", "item2"],
                        },
                        "steps": [{"title": "one"}, {"title": "two"}, {"title": "three"}],
                        "risks_mitigations": ["Risk 1", "Risk 2"],
                    }
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "PLAN" in output
        assert "Test plan" in output
        assert "item1" in output
        assert "item2" in output
        assert "3" in output  # 3 steps
        assert "Risk 1" in output

    def test_renders_full_markdown_handoff_when_plan_md_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        plan_markdown = (
            "# Implementation Plan\n\n"
            "## Steps\n"
            "1. Add regression tests\n"
            "2. Fix pipeline routing\n\n"
            "## Work Units\n"
            "- **api** — Update API surface\n"
        )
        (agent_dir / "PLAN.md").write_text(plan_markdown, encoding="utf-8")
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "PLAN" in output
        assert "Add regression tests" in output
        assert "Fix pipeline routing" in output
        assert "api" in output

    def test_renders_fresh_plan_handoff_when_plan_json_exists(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        artifacts_dir = agent_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (agent_dir / "PLAN.md").write_text("STALE PLAN", encoding="utf-8")
        (artifacts_dir / "plan.json").write_text(
            json.dumps(
                {
                    "type": "plan",
                    "content": {
                        "summary": {
                            "context": "Fresh plan context",
                            "scope_items": [
                                {"text": "Refresh PLAN.md before rendering"},
                                {"text": "Keep JSON authoritative"},
                                {"text": "Show the full handoff to users"},
                            ],
                        },
                        "steps": [
                            {
                                "number": 1,
                                "title": "Render the fresh handoff",
                                "content": "Do not trust stale markdown",
                            }
                        ],
                        "critical_files": {
                            "primary_files": [
                                {"path": "ralph/display/artifact_renderer.py", "action": "modify"}
                            ]
                        },
                        "risks_mitigations": [
                            {"risk": "Stale plan", "mitigation": "Regenerate from JSON"}
                        ],
                        "verification_strategy": [
                            {"method": "pytest", "expected_outcome": "fresh handoff renders"}
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "Fresh plan context" in output
        assert "STALE PLAN" not in output

    def test_emits_hint_when_file_absent(self, tmp_path: Path) -> None:
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "[plan]" in output
        assert "no plan artifact on disk" in output

    def test_emits_hint_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "plan.json").write_text("not valid json{", encoding="utf-8")
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "[plan]" in output
        assert "no plan artifact on disk" in output


class TestRenderAnalysisDecision:
    def test_renders_analysis_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "DEVELOPMENT_ANALYSIS_DECISION.md").write_text(
            "STALE ANALYSIS",
            encoding="utf-8",
        )
        artifacts_dir = agent_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "development_analysis_decision.json").write_text(
            json.dumps(
                {
                    "type": "development_analysis_decision",
                    "content": {
                        "status": "request_changes",
                        "summary": "Use the freshly regenerated handoff.",
                        "how_to_fix": ["Do not trust stale markdown."],
                    },
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_analysis_decision(tmp_path, "development_analysis", ctx)
        output = _console_output(ctx.console)
        assert "ANALYSIS: development_analysis" in output
        assert "Use the freshly regenerated handoff." in output
        assert "STALE ANALYSIS" not in output

    def test_renders_analysis_block_when_file_present(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "development_analysis_decision.json").write_text(
            json.dumps(
                {
                    "type": "development_analysis_decision",
                    "content": {
                        "status": "approved",
                        "summary": "Code looks good",
                    },
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_analysis_decision(tmp_path, "development_analysis", ctx)
        output = _console_output(ctx.console)
        assert "ANALYSIS: development_analysis" in output
        assert "approved" in output
        assert "Code looks good" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        ctx = _make_display_context()
        render_analysis_decision(tmp_path, "nonexistent_phase", ctx)
        output = _console_output(ctx.console)
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "review_analysis_decision.json").write_text(
            "invalid json{{{",
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_analysis_decision(tmp_path, "review_analysis", ctx)
        output = _console_output(ctx.console)
        assert output == ""


class TestRenderCommitMessage:
    def test_renders_commit_block_when_file_present(self, tmp_path: Path) -> None:
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        # Write as commit_message.json with proper MCP artifact structure
        # Note: Artifact.to_dict() uses "type" not "artifact_type"
        artifact = {
            "name": "commit_message",
            "type": "commit_message",
            "content": {
                "type": "commit",
                "subject": "feat: add new feature",
                "body": "This is the commit body\nwith multiple lines",
            },
            "created_at": "2026-04-19T12:00:00Z",
            "updated_at": "2026-04-19T12:00:00Z",
        }
        (tmp_dir / "commit_message.json").write_text(json.dumps(artifact), encoding="utf-8")
        ctx = _make_display_context()
        render_commit_message(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "COMMIT MESSAGE" in output
        assert "feat: add new feature" in output
        assert "This is the commit body" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        ctx = _make_display_context()
        render_commit_message(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "commit_message.json").write_text("not json at all", encoding="utf-8")
        ctx = _make_display_context()
        render_commit_message(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert output == ""


class TestRenderDevelopmentArtifact:
    def test_renders_development_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "DEVELOPMENT_RESULT.md").write_text(
            "# Development Result\n\n## Summary\n\nImplemented the feature.\n",
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_development_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "DEVELOPMENT RESULT" in output
        assert "Implemented the feature." in output


class TestRenderReviewArtifact:
    def test_renders_review_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "ISSUES.md").write_text(
            "# Review Issues\n\n## Summary\n\nReview found gaps.\n",
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_review_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "REVIEW ISSUES" in output
        assert "Review found gaps." in output

    def test_renders_fresh_review_handoff_when_issues_json_exists(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        artifacts_dir = agent_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (agent_dir / "ISSUES.md").write_text("STALE ISSUES", encoding="utf-8")
        (artifacts_dir / "issues.json").write_text(
            json.dumps(
                {
                    "type": "issues",
                    "content": {
                        "status": "issues_found",
                        "summary": "Fresh review findings.",
                        "issues": [
                            {
                                "path": "ralph/pipeline/runner.py",
                                "severity": "high",
                                "summary": "Refresh ISSUES.md before rendering.",
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_review_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "Fresh review findings." in output
        assert "STALE ISSUES" not in output


class TestRenderFixArtifact:
    def test_renders_fix_block_when_issues_file_present(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "issues.json").write_text(
            json.dumps(
                {
                    "issues": [
                        {"description": "Bug in foo"},
                        {"description": "Bug in bar"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_fix_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "FIX" in output
        assert "2 issue(s) addressed" in output
        assert "Bug in foo" in output
        assert "Bug in bar" in output

    def test_renders_fix_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "FIX_RESULT.md").write_text(
            "# Fix Result\n\n## Summary\n\nApplied the fixes.\n",
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_fix_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "FIX" in output
        assert "Applied the fixes." in output

    def test_renders_fix_block_when_fix_result_present(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "fix_result.json").write_text(
            json.dumps(
                {
                    "type": "fix_result",
                    "content": {
                        "summary": "Applied the fixes.",
                        "files_changed": "- file1.txt\n- file2.py",
                    },
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_fix_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "FIX" in output
        assert "Applied the fixes." in output

    def test_no_output_when_no_file_present(self, tmp_path: Path) -> None:
        ctx = _make_display_context()
        render_fix_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "issues.json").write_text("broken json", encoding="utf-8")
        ctx = _make_display_context()
        render_fix_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert output == ""
