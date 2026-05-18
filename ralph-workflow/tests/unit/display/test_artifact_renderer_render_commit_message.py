from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
    render_commit_message,
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
