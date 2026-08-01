"""Black-box tests for ``ParallelDisplay.emit_config_table`` (wt-007).

Pins the new effective-config-panel emit method. The test is
black-box: it constructs a StringIO-backed rich Console, attaches a
DisplayContext, and asserts the visible output. No real I/O, no
time.sleep, no subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import Mock

from rich.console import Console
from rich.panel import Panel

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME


def _display() -> tuple[ParallelDisplay, StringIO, list[object]]:
    buf = StringIO()
    captured: list[object] = []
    console = Console(
        file=buf,
        force_terminal=False,
        width=120,
        color_system=None,
        theme=RALPH_THEME,
    )

    class _CaptureConsole:
        width = 120
        file = buf

        def print(self, *args: object, **kwargs: object) -> None:
            captured.extend(args)
            console.print(*args, **kwargs)

    cap_console = _CaptureConsole()
    ctx = make_display_context(console=cap_console, env={})
    return ParallelDisplay(ctx), buf, captured


def test_emit_config_table_renders_panel() -> None:
    """Real UnifiedConfig renders a Panel with the section rule and title."""
    pd, buf, captured = _display()
    config = UnifiedConfig()
    pd.emit_config_table(config)
    pd.stop()
    output = buf.getvalue()
    panels = [item for item in captured if isinstance(item, Panel)]
    assert len(panels) == 1, f"expected exactly 1 panel, got {len(panels)}: {panels!r}"
    panel = panels[0]
    assert panel.title == "Effective Configuration", f"unexpected panel title: {panel.title!r}"
    assert "[config]" in output, f"expected [config] section rule: {output!r}"
    assert "Effective Configuration" in output, f"missing panel title: {output!r}"


def test_emit_config_table_renders_panel_via_mock_spec() -> None:
    """Mock spec path: a ``Mock(spec=UnifiedConfig)`` still renders the Panel.

    Documents the contract: callers may use a ``Mock(spec=UnifiedConfig)``
    stand-in when constructing a real ``UnifiedConfig`` is heavy in
    test contexts. The mock's ``model_dump_json(indent=2)`` returns
    ``'{}'`` (the default ``Mock`` return value), and the panel
    renders with that empty JSON body.
    """
    pd, _buf, captured = _display()
    config = Mock(spec=UnifiedConfig)
    pd.emit_config_table(config)
    pd.stop()
    panels = [item for item in captured if isinstance(item, Panel)]
    assert len(panels) == 1, f"expected exactly 1 panel from mock, got {len(panels)}"


# --- DA-004 (wt-028-display S-6 / AC-05): height-constrained config
# table degradation. At the canonical 12-row floor and below, the
# bordered Panel around the full config JSON degrades to an unboxed
# headed summary that lists the top-level config keys. The bordered
# form would consume the entire 12-row working area; the heading-only
# form keeps the section rule + a condensed key list so the operator
# still sees the effective configuration structure.
# -------------------------------------------------------------------------


def _height_aware_display(*, height: int) -> tuple[ParallelDisplay, StringIO, list[object]]:
    """Build a display whose Console carries the requested ``height``.

    The :class:`rich.console.Console` is created with
    ``height=height`` and a small ``file=StringIO`` so the
    rendered output is capturable. The ``force_height`` argument
    on :func:`make_display_context` is required because Rich's
    ``Console.size.height`` does not always reflect the
    constructor's ``height`` kwarg in a non-terminal context
    (it can be ``None`` until the Console is recorded / printed).
    Pinning ``force_height`` is the documented precedence path
    for short-terminal testing.
    """
    buf = StringIO()
    captured: list[object] = []
    _height_value = height
    console = Console(
        file=buf,
        force_terminal=False,
        width=120,
        height=height,
        color_system=None,
        theme=RALPH_THEME,
    )

    class _CaptureConsole:
        width = 120
        height = _height_value
        file = buf
        size = type("S", (), {"width": 120, "height": _height_value})()

        def print(self, *args: object, **kwargs: object) -> None:
            captured.extend(args)
            console.print(*args, **kwargs)

        def rule(self, *args: object, **kwargs: object) -> None:
            captured.append(("rule", args, kwargs))
            console.rule(*args, **kwargs)

    cap_console = _CaptureConsole()
    ctx = make_display_context(console=cap_console, env={}, force_height=_height_value)
    return ParallelDisplay(ctx), buf, captured


def test_emit_config_table_degrades_to_unboxed_at_12_rows() -> None:
    """DA-004: at the canonical 12-row floor the Panel becomes an unboxed heading.

    The canonical 12-row floor is the documented accessibility path
    (large-text / magnified / braille displays); the framed
    presentation must give way to unboxed headed text there, not one
    row later. The section rule, the title, and the top-level
    config keys survive; the bordered Panel is dropped.
    """
    pd, buf, captured = _height_aware_display(height=12)
    config = UnifiedConfig()
    pd.emit_config_table(config)
    pd.stop()
    output = buf.getvalue()
    # Section rule + heading survive.
    assert "[config]" in output, f"section rule missing at 12 rows:\n{output!r}"
    assert "Effective Configuration" in output, f"heading missing at 12 rows:\n{output!r}"
    # No bordered Panel.
    panels = [item for item in captured if isinstance(item, Panel)]
    assert len(panels) == 0, (
        f"boxed Panel must be dropped at 12 rows; got {len(panels)}: {panels!r}"
    )
    # No panel corner characters anywhere in the output.
    for corner in ("╭", "╮", "╰", "╯", "┌", "┐", "└", "┘"):
        assert corner not in output, f"panel corner {corner!r} survived at 12 rows:\n{output!r}"


def test_emit_config_table_keeps_panel_at_24_rows() -> None:
    """DA-004: at height=24 the full bordered Panel survives."""
    pd, _buf, captured = _height_aware_display(height=24)
    config = UnifiedConfig()
    pd.emit_config_table(config)
    pd.stop()
    panels = [item for item in captured if isinstance(item, Panel)]
    assert len(panels) == 1, f"boxed Panel must survive at 24 rows; got {len(panels)}: {panels!r}"
    panel = panels[0]
    assert panel.title == "Effective Configuration"


def test_emit_config_table_quiet_mode_emits_nothing() -> None:
    """Quiet mode produces no output."""
    pd, buf, captured = _display()
    pd._is_quiet = True
    pd.emit_config_table(UnifiedConfig())
    pd.stop()
    assert buf.getvalue() == "", f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    assert captured == [], f"quiet mode must not call console.print, got: {captured!r}"
