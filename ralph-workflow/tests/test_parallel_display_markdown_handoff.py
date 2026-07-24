"""ParallelDisplay artifact rendering reads submitted markdown directly.

Markdown artifacts are the source of truth: submission writes the handoff file
as identical bytes, so the display must render the existing markdown (handoff
copy first, artifact document as fallback) without deriving anything from JSON.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path


def _display() -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    return ParallelDisplay(make_display_context(console=console, env={"CI": "1"})), buf


def test_emit_development_artifact_renders_markdown_handoff(tmp_path: Path) -> None:
    handoff = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(
        "---\ntype: development_result\n---\n## Summary\n- [S1] Implemented the feature\n",
        encoding="utf-8",
    )
    display, buf = _display()

    display.emit_development_artifact(tmp_path)

    text = buf.getvalue()
    assert "DEVELOPMENT RESULT" in text
    assert "Implemented the feature" in text


def test_emit_review_artifact_falls_back_to_markdown_artifact_document(tmp_path: Path) -> None:
    artifact = tmp_path / ".agent" / "artifacts" / "issues.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        "---\ntype: issues\nstatus: request_changes\n---\n## Summary\n- [S1] Found a defect\n",
        encoding="utf-8",
    )
    display, buf = _display()

    display.emit_review_artifact(tmp_path)

    text = buf.getvalue()
    assert "REVIEW ISSUES" in text
    assert "Found a defect" in text


def test_parallel_display_regression_development_ignores_retired_json_artifact(
    tmp_path: Path,
) -> None:
    """Cover the markdown-migration task: JSON is not a display fallback."""
    artifact = tmp_path / ".agent" / "artifacts" / "development_result.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"summary":"retired development payload"}', encoding="utf-8")
    display, buf = _display()

    display.emit_development_artifact(tmp_path)

    assert "retired development payload" not in buf.getvalue()


def test_parallel_display_regression_review_ignores_retired_json_artifact(
    tmp_path: Path,
) -> None:
    """Cover the markdown-migration task: JSON is not a display fallback."""
    artifact = tmp_path / ".agent" / "artifacts" / "issues.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"summary":"retired review payload"}', encoding="utf-8")
    display, buf = _display()

    display.emit_review_artifact(tmp_path)

    assert "retired review payload" not in buf.getvalue()


def test_parallel_display_regression_fix_ignores_retired_json_artifacts(
    tmp_path: Path,
) -> None:
    """Cover the markdown-migration task: JSON is not a display fallback."""
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "fix_result.json").write_text(
        '{"fixed":["retired fix payload"]}',
        encoding="utf-8",
    )
    (artifacts / "issues.json").write_text(
        '{"issues":[{"description":"retired issues payload"}]}',
        encoding="utf-8",
    )
    display, buf = _display()

    display.emit_fix_artifact(tmp_path)

    text = buf.getvalue()
    assert "retired fix payload" not in text
    assert "retired issues payload" not in text


def test_emit_plan_artifact_hints_when_nothing_on_disk(tmp_path: Path) -> None:
    display, buf = _display()

    display.emit_plan_artifact(tmp_path)

    assert "(no plan artifact on disk)" in buf.getvalue()
