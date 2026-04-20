from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
    render_analysis_decision,
    render_commit_message,
    render_fix_artifact,
    render_plan_artifact,
)


def _make_console() -> Console:
    return Console(file=StringIO(), force_terminal=True, color_system=None, width=120)


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
        output = console.file.getvalue()
        assert "PLAN" in output
        assert "Test plan" in output
        assert "item1" in output
        assert "item2" in output
        assert "3" in output  # 3 steps
        assert "Risk 1" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        console = _make_console()
        render_plan_artifact(tmp_path, console)
        output = console.file.getvalue()
        # Missing file → no output per spec
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "plan.json").write_text("not valid json{", encoding="utf-8")
        console = _make_console()
        render_plan_artifact(tmp_path, console)
        output = console.file.getvalue()
        # Malformed → no output per spec (defensive)
        assert output == ""


class TestRenderAnalysisDecision:
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
        output = console.file.getvalue()
        assert "ANALYSIS: development_analysis" in output
        assert "approved" in output
        assert "Code looks good" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        console = _make_console()
        render_analysis_decision(tmp_path, "nonexistent_phase", console)
        output = console.file.getvalue()
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
        output = console.file.getvalue()
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
        output = console.file.getvalue()
        assert "COMMIT MESSAGE" in output
        assert "feat: add new feature" in output
        assert "This is the commit body" in output

    def test_no_output_when_file_absent(self, tmp_path: Path) -> None:
        console = _make_console()
        render_commit_message(tmp_path, console)
        output = console.file.getvalue()
        # Missing file → no output per spec
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "commit_message.json").write_text("not json at all", encoding="utf-8")
        console = _make_console()
        render_commit_message(tmp_path, console)
        output = console.file.getvalue()
        # Malformed → no output per spec (defensive)
        assert output == ""


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
        output = console.file.getvalue()
        assert "FIX" in output
        assert "2 issue(s) addressed" in output
        assert "Bug in foo" in output
        assert "Bug in bar" in output

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
        output = console.file.getvalue()
        assert "FIX" in output
        assert "2 item(s) fixed" in output

    def test_no_output_when_no_file_present(self, tmp_path: Path) -> None:
        console = _make_console()
        render_fix_artifact(tmp_path, console)
        output = console.file.getvalue()
        # No file → no output per spec
        assert output == ""

    def test_no_output_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "issues.json").write_text("broken json", encoding="utf-8")
        console = _make_console()
        render_fix_artifact(tmp_path, console)
        output = console.file.getvalue()
        # Malformed → no output per spec (defensive)
        assert output == ""
