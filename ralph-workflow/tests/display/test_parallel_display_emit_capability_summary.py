"""Black-box tests for ``ParallelDisplay.emit_capability_summary`` (wt-007).

Pins the new capability-summary emit method. The test is black-box:
it constructs a StringIO-backed rich Console, attaches a
DisplayContext, and asserts the visible output. No real I/O, no
time.sleep, no subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_state import CapabilityState
from ralph.skills._capability_status import CapabilityStatus


def _display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        width=120,
        color_system=None,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def _healthy_state() -> CapabilityState:
    """Build a CapabilityState with all four entries in INSTALLED_HEALTHY."""
    healthy = CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY)
    return CapabilityState(
        web_search=healthy,
        visit_url=healthy,
        docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
        skills=healthy,
    )


def test_emit_capability_summary_renders_baseline_capabilities_header() -> None:
    """AC-05: capability summary renders the section rule and table title."""
    pd, buf = _display()
    pd.emit_capability_summary(_healthy_state())
    pd.stop()
    output = buf.getvalue()
    assert "[capabilities]" in output, (
        f"expected [capabilities] section rule in output: {output!r}"
    )
    assert "Baseline Capabilities" in output, (
        f"expected 'Baseline Capabilities' title in output: {output!r}"
    )


def test_emit_capability_summary_quiet_mode_emits_nothing() -> None:
    """AC-05: quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_capability_summary(_healthy_state())
    pd.stop()
    assert buf.getvalue() == "", (
        f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    )
