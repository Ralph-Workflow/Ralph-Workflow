"""Black-box tests for the diagnose table emit methods (wt-007).

Pins the new ``emit_diagnose_inventory_table``,
``emit_diagnose_probe_table``, and ``emit_diagnose_servers_table``
methods. Each method takes a ``Sequence[tuple[object, ...]]`` and
renders a rich.table.Table with a section-rule header. The test is
black-box: it constructs a StringIO-backed rich Console, attaches a
DisplayContext, and asserts the visible output. No real I/O, no
time.sleep, no subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 1.0s.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME


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


# ---------------------------------------------------------------------------
# emit_diagnose_inventory_table
# ---------------------------------------------------------------------------


def test_emit_diagnose_inventory_table_with_one_row() -> None:
    """Single row renders with the section rule, title, and all 4 cells."""
    pd, buf = _display()
    pd.emit_diagnose_inventory_table(
        [("server-a", "workspace", "stdio", "local")]
    )
    pd.stop()
    output = buf.getvalue()
    assert "[diagnose-inventory]" in output, (
        f"expected section rule in output: {output!r}"
    )
    assert "Effective Session MCP Inventory" in output, (
        f"missing table title: {output!r}"
    )
    for cell in ("server-a", "workspace", "stdio", "local"):
        assert cell in output, f"missing cell {cell!r}: {output!r}"


def test_emit_diagnose_inventory_table_with_empty_rows() -> None:
    """Empty rows list still emits the title and section rule (body is empty)."""
    pd, buf = _display()
    pd.emit_diagnose_inventory_table([])
    pd.stop()
    output = buf.getvalue()
    assert "[diagnose-inventory]" in output, (
        f"expected section rule in output: {output!r}"
    )
    assert "Effective Session MCP Inventory" in output, (
        f"missing table title: {output!r}"
    )


def test_emit_diagnose_inventory_table_quiet_mode() -> None:
    """Quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_diagnose_inventory_table(
        [("server-a", "workspace", "stdio", "local")]
    )
    pd.stop()
    assert buf.getvalue() == "", (
        f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    )


# ---------------------------------------------------------------------------
# emit_diagnose_probe_table
# ---------------------------------------------------------------------------


def test_emit_diagnose_probe_table_with_one_row() -> None:
    """Single row renders with the section rule, title, and all 5 cells."""
    pd, buf = _display()
    pd.emit_diagnose_probe_table(
        [("server-a", "yes", "no", "yes", "no")]
    )
    pd.stop()
    output = buf.getvalue()
    assert "[diagnose-probe]" in output, (
        f"expected section rule in output: {output!r}"
    )
    assert "Agent Transport Compatibility" in output, (
        f"missing table title: {output!r}"
    )
    for cell in ("server-a", "yes", "no"):
        assert cell in output, f"missing cell {cell!r}: {output!r}"


def test_emit_diagnose_probe_table_quiet_mode() -> None:
    """Quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_diagnose_probe_table(
        [("server-a", "yes", "no", "yes", "no")]
    )
    pd.stop()
    assert buf.getvalue() == "", (
        f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    )


# ---------------------------------------------------------------------------
# emit_diagnose_servers_table
# ---------------------------------------------------------------------------


def test_emit_diagnose_servers_table_with_one_row() -> None:
    """Single row renders with the section rule, title, and all 5 cells."""
    pd, buf = _display()
    pd.emit_diagnose_servers_table(
        [("server-a", "stdio", "healthy", "5", "ok")]
    )
    pd.stop()
    output = buf.getvalue()
    assert "[diagnose-servers]" in output, (
        f"expected section rule in output: {output!r}"
    )
    assert "Custom MCP Servers" in output, f"missing table title: {output!r}"
    for cell in ("server-a", "stdio", "healthy", "5", "ok"):
        assert cell in output, f"missing cell {cell!r}: {output!r}"


def test_emit_diagnose_servers_table_quiet_mode() -> None:
    """Quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_diagnose_servers_table(
        [("server-a", "stdio", "healthy", "5", "ok")]
    )
    pd.stop()
    assert buf.getvalue() == "", (
        f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    )
