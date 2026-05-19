from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
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
