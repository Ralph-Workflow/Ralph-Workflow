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
    render_fix_artifact,
    render_plan_artifact,
    render_review_artifact,
)


def _make_console() -> Console:
    return Console(
        file=cast("StringIO", StringIO()),
        force_terminal=True,
        color_system=None,
        width=120,
    )


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
        console = _make_console()
        render_plan_artifact(tmp_path, console)
        output = _console_output(console)
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
        console = _make_console()
        render_plan_artifact(tmp_path, console)
        output = _console_output(console)
        assert "PLAN" in output
        assert "Add regression tests" in output
        assert "Fix pipeline routing" in output
        assert "api" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        console = _make_console()
        render_plan_artifact(tmp_path, console)
        output = _console_output(console)
        # Missing file → no output per spec
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "plan.json").write_text("not valid json{", encoding="utf-8")
        console = _make_console()
        render_plan_artifact(tmp_path, console)
        output = _console_output(console)
        # Malformed → no output per spec (defensive)
        assert output == ""


class TestRenderAnalysisDecision:
    def test_renders_analysis_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "DEVELOPMENT_ANALYSIS_DECISION.md").write_text(
            "# Development Analysis Decision\n\n## Summary\n\nUse the markdown handoff.\n",
            encoding="utf-8",
        )
        artifacts_dir = agent_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "development_analysis_decision.json").write_text(
            json.dumps(
                {
                    "decision": "approved",
                    "reason": "Stale JSON should not win",
                    "timestamp": "2026-04-19T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        console = _make_console()
        render_analysis_decision(tmp_path, "development_analysis", console)
        output = _console_output(console)
        assert "ANALYSIS: development_analysis" in output
        assert "Use the markdown handoff." in output
        assert "Stale JSON should not win" not in output

    def test_renders_analysis_block_when_file_present(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "development_analysis_decision.json").write_text(
            json.dumps(
                {
                    "decision": "approved",
                    "reason": "Code looks good",
                    "timestamp": "2026-04-19T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        console = _make_console()
        render_analysis_decision(tmp_path, "development_analysis", console)
        output = _console_output(console)
        assert "ANALYSIS: development_analysis" in output
        assert "approved" in output
        assert "Code looks good" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        console = _make_console()
        render_analysis_decision(tmp_path, "nonexistent_phase", console)
        output = _console_output(console)
        # Missing file → no output per spec
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "review_analysis_decision.json").write_text(
            "invalid json{{{",
            encoding="utf-8",
        )
        console = _make_console()
        render_analysis_decision(tmp_path, "review_analysis", console)
        output = _console_output(console)
        # Malformed → no output per spec (defensive)
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
        console = _make_console()
        render_commit_message(tmp_path, console)
        output = _console_output(console)
        assert "COMMIT MESSAGE" in output
        assert "feat: add new feature" in output
        assert "This is the commit body" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        console = _make_console()
        render_commit_message(tmp_path, console)
        output = _console_output(console)
        # Missing file → no output per spec
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "commit_message.json").write_text("not json at all", encoding="utf-8")
        console = _make_console()
        render_commit_message(tmp_path, console)
        output = _console_output(console)
        # Malformed → no output per spec (defensive)
        assert output == ""


class TestRenderReviewArtifact:
    def test_renders_review_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "ISSUES.md").write_text(
            "# Review Issues\n\n## Summary\n\nReview found gaps.\n",
            encoding="utf-8",
        )
        console = _make_console()
        render_review_artifact(tmp_path, console)
        output = _console_output(console)
        assert "REVIEW ISSUES" in output
        assert "Review found gaps." in output


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
        console = _make_console()
        render_fix_artifact(tmp_path, console)
        output = _console_output(console)
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
        console = _make_console()
        render_fix_artifact(tmp_path, console)
        output = _console_output(console)
        assert "FIX" in output
        assert "Applied the fixes." in output

    def test_renders_fix_block_when_fix_result_present(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "fix_result.json").write_text(
            json.dumps(
                {
                    "fixed": ["file1.txt", "file2.py"],
                }
            ),
            encoding="utf-8",
        )
        console = _make_console()
        render_fix_artifact(tmp_path, console)
        output = _console_output(console)
        assert "FIX" in output
        assert "2 item(s) fixed" in output

    def test_no_output_when_no_file_present(self, tmp_path: Path) -> None:
        console = _make_console()
        render_fix_artifact(tmp_path, console)
        output = _console_output(console)
        # No file → no output per spec
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "issues.json").write_text("broken json", encoding="utf-8")
        console = _make_console()
        render_fix_artifact(tmp_path, console)
        output = _console_output(console)
        # Malformed → no output per spec (defensive)
        assert output == ""
