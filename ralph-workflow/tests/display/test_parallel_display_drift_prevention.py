"""Black-box drift-prevention tests for ParallelDisplay consolidation.

wt-007-consolidate-display: pins the 5 TRUE escape hatches that were
closed in this refactor so any future commit re-introducing the
bypass fails loudly. Each test runs in < 0.1 s and uses no real I/O.

=============================================================================
AUDIT (carried from step 1 of PLAN.md)
=============================================================================
The 5 TRUE escape hatches and the reachability verdict for each:

  1. ralph-workflow/ralph/pipeline/run_loop.py:344
     Was: active_display.console.print(f"\\n{RUN_COMPLETION_STAR_CTA}")
     Fix: active_display.emit(unit_id="run", line=f"\\n{RUN_COMPLETION_STAR_CTA}")
     Reachability: HIGH — fires on every successful run inside
     `if exit_code == 0:`.

  2. ralph-workflow/ralph/pipeline/plumbing/commit_plumbing.py:703
     Was: console.print(rendered) (where console = ctx.console; local alias)
     Fix: display_context.console.print(rendered, markup=False, highlight=False, no_wrap=True)
     Reachability: MEDIUM — fires inside `if verbose:` block of
     `collect_commit_agent_output`; reachable on `ralph commit --verbose`.

  3. ralph-workflow/ralph/display/plain_renderer/_plain_log_renderer.py:781
     Was: self._console.out(f"[{unit_id}] status={sanitized}")
     Fix: badge-contract self._console.print via self._build_line
     Reachability: HIGH — fires on every WorkerStatus transition via
     `def emit_status_line` (line 779); reachable on every parallel run.

  4. ralph-workflow/ralph/config/welcome.py:204
     Was: rich_console.print(panel) — Panel constructed and printed
     outside ralph/display/ via the dead `console: object` parameter
     of `emit_first_run_welcome`.
     Fix: extracted `render_first_run_panel(content, display_context)`
     into ralph/display/first_run_panel.py; dropped the dead `console`
     parameter from `emit_first_run_welcome`.
     Reachability: HIGH — fires on every fresh install or
     `--regenerate-config` inside `if has_new_or_regenerated:`.

  5. ralph-workflow/ralph/banner.py:90
     Was: dual-API signature with `console: SupportsPrint | None = None`
     alongside the required `display_context: DisplayContext` parameter.
     Fix: removed the `console` parameter; `show_banner` now uses
     `display_context.console` exclusively.
     Reachability: HIGH — every caller passing `console=` reached the
     bypass path; after the refactor no caller can.

This file also pins the ParallelDisplay status-line visual contract
restored in step 3 (Edit 1): `emit_status_line` must use the
`_build_line` + INFO/META badge contract so log parsers find status
lines via the `[status][unit_id]` tag like every other log line.
=============================================================================
"""

from __future__ import annotations

import ast
import inspect
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ralph.banner import show_banner
from ralph.display.context import make_display_context
from ralph.display.first_run_panel import render_first_run_panel
from ralph.display.parallel_display import ParallelDisplay

_REPO_ROOT = Path(__file__).parent.parent.parent
_RUN_LOOP_PATH = _REPO_ROOT / "ralph" / "pipeline" / "run_loop.py"
_COMMIT_PLUMBING_PATH = _REPO_ROOT / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"


def test_run_loop_does_not_use_active_display_console_print() -> None:
    """Pin Update 1: no `active_display.console.print(...)` left in run_loop.py."""
    tree = ast.parse(_RUN_LOOP_PATH.read_text(encoding="utf-8"))
    violators = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "print"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "console"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "active_display"
    ]
    assert not violators, (
        "active_display.console.print(...) re-introduced at lines "
        f"{violators!r} in {_RUN_LOOP_PATH.relative_to(_REPO_ROOT)}; "
        "route through active_display.emit(...) instead."
    )


def test_commit_plumbing_uses_display_context_console() -> None:
    """Pin Update 2: commit_plumbing routes verbose output via display_context.console.print."""
    tree = ast.parse(_COMMIT_PLUMBING_PATH.read_text(encoding="utf-8"))
    matches = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "print"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "console"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "display_context"
    ]
    assert matches, (
        "expected at least one display_context.console.print(...) call in "
        f"{_COMMIT_PLUMBING_PATH.relative_to(_REPO_ROOT)}; the verbose "
        "commit plumbing must route through DisplayContext."
    )


def test_plain_log_renderer_status_line_uses_build_line() -> None:
    """Pin Edit 1 of step 3: emit_status_line uses the INFO META badge contract."""
    buffer = StringIO()
    console = Console(
        file=buffer,
        force_terminal=False,
        color_system=None,
        width=120,
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    pd.set_status("unit-1", "running")
    pd.stop()
    output = buffer.getvalue()
    assert "INFO" in output, f"status line missing INFO badge: {output!r}"
    assert "META" in output, f"status line missing META badge: {output!r}"
    assert "[status][unit-1]" in output, (
        f"status line missing [status][unit-1] tag: {output!r}"
    )
    assert "running" in output, f"status text 'running' missing: {output!r}"


def test_first_run_panel_helper_routes_through_display_context() -> None:
    """Pin Update 4 of step 4: render_first_run_panel uses display_context.console only."""
    printed: list[object] = []

    class _RecordingConsole:
        width = 120
        file = StringIO()

        def print(self, *args: object, **kwargs: object) -> None:
            printed.extend(args)

    recording_console = _RecordingConsole()
    ctx = make_display_context(env={}, console=recording_console)
    render_first_run_panel([Text("hello")], display_context=ctx)

    assert len(printed) == 1, (
        f"render_first_run_panel should print exactly one Panel, got {len(printed)}: "
        f"{printed!r}"
    )
    panel = printed[0]
    assert isinstance(panel, Panel), (
        f"render_first_run_panel should print a rich.panel.Panel, got {type(panel).__name__}"
    )
    assert panel.title == "Ralph Workflow first-run setup", (
        f"panel title should be 'Ralph Workflow first-run setup', got {panel.title!r}"
    )

    source = inspect.getsource(render_first_run_panel)
    assert "Console(" not in source, (
        "render_first_run_panel must NOT construct its own Console; "
        f"found 'Console(' in source:\n{source!r}"
    )


def test_show_banner_rejects_console_parameter() -> None:
    """Pin step 5: show_banner signature is `(*, display_context, version)`, no console param."""
    sig = inspect.signature(show_banner)
    assert "console" not in sig.parameters, (
        f"show_banner must NOT accept a 'console' parameter; got: {sig}"
    )
    assert set(sig.parameters) == {"display_context", "version"}, (
        f"show_banner parameters must be exactly {{'display_context', 'version'}}; "
        f"got {set(sig.parameters)!r}"
    )
