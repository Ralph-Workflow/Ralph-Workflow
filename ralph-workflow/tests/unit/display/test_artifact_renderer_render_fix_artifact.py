from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
    render_fix_artifact,
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
