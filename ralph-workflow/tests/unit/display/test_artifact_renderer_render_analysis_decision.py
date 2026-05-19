from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
    render_analysis_decision,
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
