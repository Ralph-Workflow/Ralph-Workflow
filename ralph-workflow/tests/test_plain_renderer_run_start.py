"""Tests for PlainLogRenderer.emit_run_start."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.plain_renderer import _TAG_CATEGORY, _TAGS, PlainLogRenderer, RunStartOrientation


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return PlainLogRenderer(make_display_context(console=console, env={})), buf


def _orientation(**kwargs: object) -> RunStartOrientation:
    defaults: dict[str, object] = {
        "plan_present": False,
    }
    defaults.update(kwargs)
    return RunStartOrientation(**defaults)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_emit_run_start_prints_milestone_header() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation(plan_present=True))
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one non-empty line"
    assert "MILESTONE META [run-start]" in lines[0]
    assert "Ralph Workflow run start" in lines[0]


def test_emit_run_start_emits_prompt_line_when_present() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation(prompt_path="PROMPT.md"))
    out = buf.getvalue()
    assert "[run-start] prompt=PROMPT.md" in out


def test_emit_run_start_omits_absent_fields() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation(workspace_root="/ws", plan_present=False))
    out = buf.getvalue()
    assert "developer=" not in out
    assert "reviewer=" not in out
    assert "iterations=" not in out
    assert "parallel=" not in out
    assert "verbosity=" not in out


def test_emit_run_start_includes_plan_present_flag() -> None:
    renderer1, buf1 = _make_renderer()
    renderer1.emit_run_start(_orientation(plan_present=True))
    assert "plan=ready" in buf1.getvalue()

    renderer2, buf2 = _make_renderer()
    renderer2.emit_run_start(_orientation(plan_present=False))
    assert "plan=absent" in buf2.getvalue()


def test_emit_run_start_includes_parallel_when_present() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation(parallel_max_workers=4))
    assert "parallel=max_workers=4" in buf.getvalue()


def test_emit_run_start_sanitises_rich_markup_in_path() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation(workspace_root="[bold]x[/bold]"))
    out = buf.getvalue()
    assert "[bold]" not in out
    assert "[/bold]" not in out
    assert "x" in out


def test_emit_run_start_no_ansi() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(
        _orientation(
            prompt_path="PROMPT.md",
            developer_agent="claude",
            developer_model="claude-3-5-sonnet",
            reviewer_agent="claude",
            reviewer_model="claude-3-5-haiku",
            developer_iters=3,
            reviewer_reviews=1,
            parallel_max_workers=2,
            plan_present=True,
            verbosity="verbose",
            workspace_root="/workspace",
        )
    )
    out = buf.getvalue()
    assert "\x1b[" not in out


def test_run_start_tag_is_registered() -> None:
    assert "run-start" in _TAGS
    assert _TAG_CATEGORY.get("run-start") == "META"


def test_emit_run_start_includes_verbosity_when_present() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation(verbosity="verbose"))
    out = buf.getvalue()
    assert "verbosity=verbose" in out


def test_emit_run_start_verbosity_omitted_when_none() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation(verbosity=None))
    out = buf.getvalue()
    assert "verbosity=" not in out


def test_emit_run_start_milestone_glyph_ascii_fallback() -> None:
    """RALPH_FORCE_ASCII=1 uses ASCII milestone glyph (* not ◆) in run-start header."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    renderer = PlainLogRenderer(
        make_display_context(console=console, env={"RALPH_FORCE_ASCII": "1"})
    )
    renderer.emit_run_start(_orientation())
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one non-empty line"
    assert "[run-start] * Ralph Workflow run start" in lines[0]
    assert "◆" not in lines[0]


def test_emit_run_start_legend_format() -> None:
    """emit_run_start legend uses new pipe-separated format."""
    renderer, buf = _make_renderer()
    renderer.emit_run_start(_orientation())
    out = buf.getvalue()
    assert "levels: INFO|SUCCESS|WARN|ERROR|MILESTONE" in out
    assert "cats: META|CONT" in out
    assert "format: [tag][unit] message" in out
