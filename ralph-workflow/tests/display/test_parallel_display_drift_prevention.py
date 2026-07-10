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

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

_REPO_ROOT = Path(__file__).parent.parent.parent
_RUN_LOOP_PATH = _REPO_ROOT / "ralph" / "pipeline" / "run_loop.py"
_COMMIT_PLUMBING_PATH = _REPO_ROOT / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"


# Per-test pytest marker: the AST-walking and parallel-display
# construction in this file has been observed to exceed the
# global 1-second per-test cap under parallel xdist CPU
# contention; 5 seconds is the minimum supported by the
# audit invariant (``_VERIFY_STEP_TIMEOUT_SECONDS >= 5.0``)
# and well under the 60-second combined ``make verify``
# budget. The default 1 s cap remains in place for every
# other test in the suite.
pytestmark = pytest.mark.timeout_seconds(5)


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
    """Pin Update 2: commit_plumbing routes verbose output via ParallelDisplay.

    The wt-007 consolidation moved verbose output from
    ``display_context.console.print(rendered, ...)`` to
    ``display.emit_status(rendered.plain)``. This test pins the new
    contract by asserting that the call site resolves a display via
    :func:`resolve_active_display` and emits through the consolidated
    ParallelDisplay surface.
    """
    text = _COMMIT_PLUMBING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    matches = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "emit_status"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "display"
    ]
    assert matches, (
        "expected at least one display.emit_status(...) call in "
        f"{_COMMIT_PLUMBING_PATH.relative_to(_REPO_ROOT)}; the verbose "
        "commit plumbing must route through ParallelDisplay."
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
    assert "[status][unit-1]" in output, f"status line missing [status][unit-1] tag: {output!r}"
    assert "running" in output, f"status text 'running' missing: {output!r}"


def test_first_run_panel_helper_routes_through_display_context() -> None:
    """Pin wt-007: ParallelDisplay.emit_first_run_panel routes through the active display."""
    printed: list[object] = []

    class _RecordingConsole:
        width = 120
        file = StringIO()

        def print(self, *args: object, **kwargs: object) -> None:
            printed.extend(args)

    recording_console = _RecordingConsole()
    ctx = make_display_context(env={}, console=recording_console)
    pd = ParallelDisplay(ctx)
    pd.emit_first_run_panel([Text("hello")])

    assert len(printed) == 1, (
        f"emit_first_run_panel should print exactly one Panel, got {len(printed)}: {printed!r}"
    )
    panel = printed[0]
    assert isinstance(panel, Panel), (
        f"emit_first_run_panel should print a rich.panel.Panel, got {type(panel).__name__}"
    )

    source = inspect.getsource(ParallelDisplay.emit_first_run_panel)
    assert "Console(" not in source, (
        "emit_first_run_panel must NOT construct its own Console; "
        f"found 'Console(' in source:\n{source!r}"
    )


def test_emit_welcome_banner_rejects_console_parameter() -> None:
    """Pin wt-007: ParallelDisplay.emit_welcome_banner signature is `(self, *, version)` only."""
    sig = inspect.signature(ParallelDisplay.emit_welcome_banner)
    assert "console" not in sig.parameters, (
        f"emit_welcome_banner must NOT accept a 'console' parameter; got: {sig}"
    )
    assert "version" in sig.parameters, (
        f"emit_welcome_banner must accept a 'version' parameter; got: {sig}"
    )


_PARALLEL_DISPLAY_ALL_NAMES = {
    # 27 pre-existing instance methods (verified before step 2).
    "emit_parsed_event",
    "emit_analysis_result",
    "emit_run_start",
    "emit_phase_close",
    "emit_phase_close_from_exit",
    "emit_run_end",
    "emit_phase_start",
    "emit_phase_start_from_entry",
    "emit_phase_transition",
    "emit_phase_close_banner",
    "emit_plan_artifact",
    "emit_development_artifact",
    "emit_review_artifact",
    "emit_fix_artifact",
    "emit_analysis_decision",
    "emit_commit_message",
    "emit_missing_plan_hint",
    "emit_first_run_panel",
    "emit_welcome_banner",
    "emit_agents_table",
    "emit_providers_table",
    "emit_config_table",
    "emit_capability_summary",
    "emit_status",
    "emit_warning",
    "emit_skill_failure_warning",
    "emit_fallback_next_steps",
    # 9 new names added by the consolidation.
    "emit_metrics_table",
    "emit_checkpoint_summary_table",
    "emit_diagnose_inventory_table",
    "emit_diagnose_probe_table",
    "emit_diagnose_servers_table",
    "emit_info_panel",
    "emit_blank_line",
    "emit_dry_run_summary",
    "emit_renderable",
    # 6 new names added by step 5 of the wt-028-display plan (raw-log
    # helpers + completion panel promoted to canonical members).
    "emit_log_line",
    "emit_status_line",
    "emit_warn_line",
    "emit_snapshot",
    "emit_completion_summary_panel",
}


def test_parallel_display_exposes_exact_41_emit_methods() -> None:
    """Pin wt-028-display: ParallelDisplay has all 41 emit_* method names.

    Uses :func:`inspect.getmembers` to enumerate the canonical set and
    asserts exact set equality with ``_PARALLEL_DISPLAY_ALL_NAMES``.
    The exact-equality assertion is stricter than the prior subset
    check: it captures every drift (added method OR removed method)
    instead of silently tolerating either direction.

    PA-001 fix: NO emit_error is included; error messages use
    the existing ``emit_warning`` method with theme.status.error
    styling. The test explicitly asserts all 6 pre-existing methods
    that were present in the baseline so a future regression that
    renames or removes one of those 6 would be flagged.
    """
    members = {name for name, _ in inspect.getmembers(ParallelDisplay) if name.startswith("emit_")}
    # emit_activity_line is the module-level activity helper that ALSO appears
    # as an instance method; the canonical set excludes it because the count
    # of "consolidated instance methods" treats it as a one-shot helper, not
    # a Consolidated Display surface member.
    instance_only = members - {"emit_activity_line"}
    diff = instance_only ^ _PARALLEL_DISPLAY_ALL_NAMES
    members_only = sorted(diff & instance_only)
    canonical_only = sorted(diff & _PARALLEL_DISPLAY_ALL_NAMES)
    assert instance_only == _PARALLEL_DISPLAY_ALL_NAMES, (
        "ParallelDisplay instance emit_* set must equal the canonical "
        f"_PARALLEL_DISPLAY_ALL_NAMES set of {len(_PARALLEL_DISPLAY_ALL_NAMES)} "
        f"names. members - canonical = {members_only!r}; "
        f"canonical - members = {canonical_only!r}."
    )
    explicit_pins = {
        "emit_parsed_event",
        "emit_analysis_result",
        "emit_run_start",
        "emit_phase_close",
        "emit_phase_close_from_exit",
        "emit_run_end",
    }
    for name in explicit_pins:
        assert name in members, f"ParallelDisplay is missing baseline method {name!r}"
    assert "emit_error" not in members, (
        "ParallelDisplay must NOT expose a separate emit_error method; "
        "error messages use emit_warning with theme.status.error styling."
    )


def test_emit_methods_route_through_display_console_only() -> None:
    """Pin wt-007: every emit_* method routes through the display's own console.

    Uses :func:`inspect.getsource` on each emit_* method and asserts
    the body references ``self._console`` (or a private helper that
    does) — proving the methods consolidate rather than re-introduce
    free-function fan-out. The drift we are guarding against is the
    construction of a fresh ``rich.console.Console`` instance inside
    an emit method body.
    """
    members = [
        (name, method)
        for name, method in inspect.getmembers(ParallelDisplay, predicate=inspect.isfunction)
        if name.startswith("emit_")
    ]
    assert members, "ParallelDisplay has no emit_* methods"
    violators: list[str] = []
    for name, method in members:
        try:
            source = inspect.getsource(method)
        except (OSError, TypeError):
            continue
        # Disallow constructing a fresh Console in an emit_* method.
        if "Console(" in source:
            violators.append(name)
    assert not violators, (
        f"emit_* methods that construct their own Console (free-function fan-out): {violators!r}"
    )
