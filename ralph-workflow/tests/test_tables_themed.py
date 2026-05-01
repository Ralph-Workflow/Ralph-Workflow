"""ANSI-vs-plain regression tests for ralph/display/tables.py."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.tables import (
    CheckpointSummaryOptions,
    show_agents,
    show_checkpoint_summary,
    show_config,
    show_metrics,
    show_providers,
)
from ralph.display.theme import RALPH_THEME

# Keep Path available for annotations referenced by the models under test.
_ = Path

GeneralConfig.model_rebuild()
UnifiedConfig.model_rebuild()


def _themed_context(buf: StringIO) -> object:
    """Create a DisplayContext for themed (color) output."""
    console = Console(
        file=buf,
        force_terminal=True,
        no_color=False,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    return make_display_context(console=console, env={})


def _plain_context(buf: StringIO) -> object:
    """Create a DisplayContext for plain (no color) output."""
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        theme=RALPH_THEME,
        width=200,
    )
    return make_display_context(console=console, env={})


def test_show_providers_emits_ansi_on_tty() -> None:
    buf = StringIO()
    ctx = _themed_context(buf)
    show_providers(["openai"], display_context=ctx)
    assert "\x1b[" in buf.getvalue()


def test_show_providers_no_ansi_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    show_providers(["openai"], display_context=ctx)
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "openai" in out
    assert "Available" in out


def test_show_agents_emits_ansi_on_tty() -> None:
    agent = AgentConfig(cmd="/usr/bin/claude", json_parser=JsonParserType.GENERIC)
    config = UnifiedConfig(agents={"claude": agent})
    buf = StringIO()
    ctx = _themed_context(buf)
    show_agents(config, display_context=ctx)
    assert "\x1b[" in buf.getvalue()


def test_show_agents_no_ansi_on_plain() -> None:
    agent = AgentConfig(cmd="/usr/bin/claude", json_parser=JsonParserType.GENERIC)
    config = UnifiedConfig(agents={"claude": agent})
    buf = StringIO()
    ctx = _plain_context(buf)
    show_agents(config, display_context=ctx)
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "claude" in out


def test_show_metrics_emits_ansi_on_tty() -> None:
    buf = StringIO()
    ctx = _themed_context(buf)
    show_metrics({"runs": 1}, display_context=ctx)
    assert "\x1b[" in buf.getvalue()


def test_show_metrics_no_ansi_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    show_metrics({"runs": 1}, display_context=ctx)
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "runs" in out
    assert "1" in out


def test_show_config_emits_ansi_on_tty() -> None:
    buf = StringIO()
    ctx = _themed_context(buf)
    show_config(UnifiedConfig(), display_context=ctx)
    assert "\x1b[" in buf.getvalue()


def test_show_config_no_ansi_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    show_config(UnifiedConfig(), display_context=ctx)
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Effective Configuration" in out


def test_show_checkpoint_summary_emits_ansi_on_tty() -> None:
    opts = CheckpointSummaryOptions(
        phase="review",
        budget_progress={"iteration": (1, 3), "reviewer_pass": (0, 2)},
    )
    buf = StringIO()
    ctx = _themed_context(buf)
    show_checkpoint_summary(opts, display_context=ctx)
    assert "\x1b[" in buf.getvalue()


def test_show_checkpoint_summary_no_ansi_on_plain() -> None:
    opts = CheckpointSummaryOptions(
        phase="review",
        budget_progress={"iteration": (1, 3), "reviewer_pass": (0, 2)},
    )
    buf = StringIO()
    ctx = _plain_context(buf)
    show_checkpoint_summary(opts, display_context=ctx)
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "review" in out
    assert "1/3" in out
