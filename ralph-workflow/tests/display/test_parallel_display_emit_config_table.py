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
    pd, buf, captured = _display()
    config = Mock(spec=UnifiedConfig)
    pd.emit_config_table(config)
    pd.stop()
    output = buf.getvalue()
    panels = [item for item in captured if isinstance(item, Panel)]
    assert len(panels) == 1, f"expected exactly 1 panel from mock, got {len(panels)}"
    assert "[config]" in output, f"expected [config] section rule: {output!r}"


def test_emit_config_table_quiet_mode_emits_nothing() -> None:
    """Quiet mode produces no output."""
    pd, buf, captured = _display()
    pd._is_quiet = True
    pd.emit_config_table(UnifiedConfig())
    pd.stop()
    assert buf.getvalue() == "", f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    assert captured == [], f"quiet mode must not call console.print, got: {captured!r}"
