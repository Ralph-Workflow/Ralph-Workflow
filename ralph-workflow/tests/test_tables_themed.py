"""ANSI-vs-plain regression tests for ralph/display/tables.py."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
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


def _themed(buf: StringIO) -> Console:
    return Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )


def _plain(buf: StringIO) -> Console:
    return Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        theme=RALPH_THEME,
        width=200,
    )


def test_show_providers_emits_ansi_on_tty() -> None:
    buf = StringIO()
    show_providers(["openai"], console=_themed(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_providers_no_ansi_on_plain() -> None:
    buf = StringIO()
    show_providers(["openai"], console=_plain(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "openai" in out
    assert "Available" in out


def test_show_agents_emits_ansi_on_tty() -> None:
    agent = AgentConfig(cmd="/usr/bin/claude", json_parser=JsonParserType.GENERIC)
    config = UnifiedConfig(agents={"claude": agent})
    buf = StringIO()
    show_agents(config, console=_themed(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_agents_no_ansi_on_plain() -> None:
    agent = AgentConfig(cmd="/usr/bin/claude", json_parser=JsonParserType.GENERIC)
    config = UnifiedConfig(agents={"claude": agent})
    buf = StringIO()
    show_agents(config, console=_plain(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "claude" in out


def test_show_metrics_emits_ansi_on_tty() -> None:
    buf = StringIO()
    show_metrics({"runs": 1}, console=_themed(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_metrics_no_ansi_on_plain() -> None:
    buf = StringIO()
    show_metrics({"runs": 1}, console=_plain(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "runs" in out
    assert "1" in out


def test_show_config_emits_ansi_on_tty() -> None:
    buf = StringIO()
    show_config(UnifiedConfig(), console=_themed(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_config_no_ansi_on_plain() -> None:
    buf = StringIO()
    show_config(UnifiedConfig(), console=_plain(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Effective Configuration" in out


def test_show_checkpoint_summary_emits_ansi_on_tty() -> None:
    opts = CheckpointSummaryOptions(
        phase="review", iteration=1, total_iterations=3,
        reviewer_pass=0, total_reviewer_passes=2,
    )
    buf = StringIO()
    show_checkpoint_summary(opts, console=_themed(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_checkpoint_summary_no_ansi_on_plain() -> None:
    opts = CheckpointSummaryOptions(
        phase="review", iteration=1, total_iterations=3,
        reviewer_pass=0, total_reviewer_passes=2,
    )
    buf = StringIO()
    show_checkpoint_summary(opts, console=_plain(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "review" in out
    assert "1/3" in out
