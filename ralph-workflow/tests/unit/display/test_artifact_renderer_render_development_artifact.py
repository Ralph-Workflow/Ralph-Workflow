from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
    render_development_artifact,
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
