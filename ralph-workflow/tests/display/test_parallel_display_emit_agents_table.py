"""Black-box tests for ``ParallelDisplay.emit_agents_table`` (wt-007).

Pins the new agent-table emit method. The test is black-box: it
constructs a StringIO-backed rich Console, attaches a DisplayContext,
and asserts the visible output. No real I/O, no time.sleep, no
subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

import types
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


def test_emit_agents_table_empty_dict() -> None:
    """Empty agents dict still renders the section rule and table title."""
    pd, buf = _display()
    pd.emit_agents_table({})
    pd.stop()
    output = buf.getvalue()
    assert "[agents]" in output, f"expected section rule in output, got: {output!r}"
    assert "Configured Agents" in output, (
        f"expected 'Configured Agents' title in output, got: {output!r}"
    )


def test_emit_agents_table_with_one_agent() -> None:
    """Single agent renders the name, command, and table title."""
    parser = types.SimpleNamespace(value="A")
    agent = types.SimpleNamespace(cmd="/usr/bin/claude", json_parser=parser, can_commit=True)
    pd, buf = _display()
    pd.emit_agents_table({"claude": agent})
    pd.stop()
    output = buf.getvalue()
    assert "claude" in output, f"missing agent name: {output!r}"
    assert "/usr/bin/claude" in output, f"missing agent command: {output!r}"
    assert "Configured Agents" in output, f"missing table title: {output!r}"
    assert "[agents]" in output, f"missing section rule: {output!r}"


def test_emit_agents_table_quiet_mode_emits_nothing() -> None:
    """Quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_agents_table({})
    pd.stop()
    assert buf.getvalue() == "", f"quiet mode must produce no output, got: {buf.getvalue()!r}"
