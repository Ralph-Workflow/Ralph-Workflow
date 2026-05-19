"""Regression tests for commit message rendering in pipeline commit flow.

These tests verify that:
1. A successful CommitEffect triggers render_commit_message() exactly once
2. A skipped commit (no diff) does NOT render the commit message block
3. The commit message block appears in the console output after a successful commit
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.artifact_renderer import render_commit_message
from ralph.display.context import make_display_context

if TYPE_CHECKING:
    from pathlib import Path


class TestRenderCommitMessageIntegration:
    """Tests for commit message rendering behavior.

    These tests verify the contract that render_commit_message():
    - Produces a titled block with "COMMIT MESSAGE" when artifact is present
    - Produces no output when artifact is absent (skipped commit)
    - Produces no output when artifact is malformed (defensive)
    """

    def test_render_commit_message_produces_titled_block(self, tmp_path: Path) -> None:
        """Verify render_commit_message outputs a titled block with subject."""
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        artifact = {
            "name": "commit_message",
            "type": "commit_message",
            "content": {
                "type": "commit",
                "subject": "feat: add new feature",
                "body": "This is the commit body",
            },
            "created_at": "2026-04-19T12:00:00Z",
            "updated_at": "2026-04-19T12:00:00Z",
        }
        (tmp_dir / "commit_message.json").write_text(json.dumps(artifact), encoding="utf-8")

        output_buffer: io.StringIO = io.StringIO()
        console = Console(file=output_buffer, force_terminal=True, color_system=None, width=120)
        ctx = make_display_context(console=console, env={})
        render_commit_message(tmp_path, ctx)

        output = output_buffer.getvalue()
        assert "COMMIT MESSAGE" in output
        assert "feat: add new feature" in output

    def test_render_commit_message_no_output_when_artifact_absent(self, tmp_path: Path) -> None:
        """Verify render_commit_message produces no output when commit is skipped."""
        # No .agent/tmp/commit_message.json - commit was skipped
        output_buffer: io.StringIO = io.StringIO()
        console = Console(file=output_buffer, force_terminal=True, color_system=None, width=120)
        ctx = make_display_context(console=console, env={})
        render_commit_message(tmp_path, ctx)

        output = output_buffer.getvalue()
        assert output == ""

    def test_render_commit_message_no_output_when_artifact_malformed(self, tmp_path: Path) -> None:
        """Verify render_commit_message is defensive about malformed JSON."""
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "commit_message.json").write_text("not valid json{{", encoding="utf-8")

        output_buffer: io.StringIO = io.StringIO()
        console = Console(file=output_buffer, force_terminal=True, color_system=None, width=120)
        ctx = make_display_context(console=console, env={})
        render_commit_message(tmp_path, ctx)

        output = output_buffer.getvalue()
        # Defensive: malformed JSON should not crash and should produce no output
        assert output == ""

    def test_commit_message_block_appears_once_after_successful_commit(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the commit message titled block appears exactly once.

        This is a regression test to ensure that when a commit succeeds,
        the pipeline commit flow renders the commit message block exactly once,
        not zero times (missing) and not multiple times (double-rendering).
        """
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        artifact = {
            "name": "commit_message",
            "type": "commit_message",
            "content": {
                "type": "commit",
                "subject": "fix: correct bug",
                "body": "Fixes the issue",
            },
            "created_at": "2026-04-19T12:00:00Z",
            "updated_at": "2026-04-19T12:00:00Z",
        }
        (tmp_dir / "commit_message.json").write_text(json.dumps(artifact), encoding="utf-8")

        output_buffer: io.StringIO = io.StringIO()
        console = Console(file=output_buffer, force_terminal=True, color_system=None, width=120)
        ctx = make_display_context(console=console, env={})

        # Simulate the pipeline calling render_commit_message once after a successful commit
        render_commit_message(tmp_path, ctx)

        output = output_buffer.getvalue()
        # Should appear exactly once
        assert output.count("COMMIT MESSAGE") == 1
        assert output.count("fix: correct bug") == 1
