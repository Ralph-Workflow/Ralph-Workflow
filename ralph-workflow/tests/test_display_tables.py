from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

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

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.context import DisplayContext

# Keep Path available for annotations referenced by the models under test.
_ = Path

GeneralConfig.model_rebuild()
UnifiedConfig.model_rebuild()


def _capture_output(func: Callable[..., None], *args: Any, **kwargs: Any) -> str:
    stream = io.StringIO()
    console = Console(file=stream, color_system=None, force_terminal=False, theme=RALPH_THEME)
    ctx = make_display_context(console=console)
    kwargs.setdefault("display_context", ctx)
    func(*args, **kwargs)
    return stream.getvalue()


def test_show_agents_displays_notice_when_no_agents() -> None:
    config = UnifiedConfig(agents={})

    output = _capture_output(show_agents, config)

    assert "Configured Agents" in output
    assert "No agents configured" in output


def test_show_agents_lists_agent_properties() -> None:
    agent = AgentConfig(
        cmd="/usr/bin/ralph-run",
        json_parser=JsonParserType.GENERIC,
        can_commit=True,
    )
    config = UnifiedConfig(agents={"alpha": agent})

    output = _capture_output(show_agents, config)

    assert "alpha" in output
    assert "/usr/bin/ralph-run" in output
    assert "generic" in output
    assert "yes" in output


def test_show_providers_reports_when_empty() -> None:
    output = _capture_output(show_providers, [])

    assert "Available Providers" in output
    assert "No providers available" in output


def test_show_providers_lists_available_providers() -> None:
    providers = ["openai", "anthropic"]

    output = _capture_output(show_providers, providers)

    assert "Available Providers" in output
    for provider in providers:
        assert provider in output
        assert "Available" in output


def test_show_config_displays_effective_json() -> None:
    config = UnifiedConfig()

    output = _capture_output(show_config, config)

    assert "Effective Configuration" in output
    assert '"general"' in output


def test_show_metrics_lists_all_metrics() -> None:
    metrics = {"runs": 5, "errors": 0}

    output = _capture_output(show_metrics, metrics)

    assert "Pipeline Metrics" in output
    assert "runs" in output
    assert "5" in output
    assert "errors" in output
    assert "0" in output


def test_show_checkpoint_summary_formats_values() -> None:
    options = CheckpointSummaryOptions(
        phase="review",
        iteration=2,
        total_iterations=7,
        reviewer_pass=1,
        total_reviewer_passes=3,
    )

    output = _capture_output(show_checkpoint_summary, options)

    assert "Checkpoint Summary" in output
    assert "Phase" in output
    assert "review" in output
    assert "Iteration" in output
    assert "2/7" in output
    assert "Review Pass" in output
    assert "1/3" in output


# --- Tests for compact mode column suppression ---


def _make_display_context_for_mode(mode: Literal["compact", "medium", "wide"]) -> DisplayContext:
    """Create a DisplayContext for the specified mode."""
    stream = io.StringIO()
    console = Console(file=stream, color_system=None, force_terminal=False, theme=RALPH_THEME)
    return make_display_context(console=console, force_mode=mode)


def test_show_agents_compact_mode_hides_parser_and_can_commit() -> None:
    """Compact mode should hide Parser and Can Commit columns."""
    agent = AgentConfig(
        cmd="/usr/bin/ralph-run",
        json_parser=JsonParserType.GENERIC,
        can_commit=True,
    )
    config = UnifiedConfig(agents={"alpha": agent})

    ctx = _make_display_context_for_mode("compact")
    stream = ctx.console.file  # type: ignore[attr-defined]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    show_agents(config, display_context=ctx)
    output = stream.getvalue()

    assert "alpha" in output
    assert "/usr/bin/ralph-run" in output
    assert "Parser" not in output
    assert "Can Commit" not in output
    assert "generic" not in output
    assert "yes" not in output


def test_show_agents_wide_mode_shows_parser_and_can_commit() -> None:
    """Wide mode should show Parser and Can Commit columns."""
    agent = AgentConfig(
        cmd="/usr/bin/ralph-run",
        json_parser=JsonParserType.GENERIC,
        can_commit=True,
    )
    config = UnifiedConfig(agents={"alpha": agent})

    ctx = _make_display_context_for_mode("wide")
    stream = ctx.console.file  # type: ignore[attr-defined]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    show_agents(config, display_context=ctx)
    output = stream.getvalue()

    assert "alpha" in output
    assert "/usr/bin/ralph-run" in output
    assert "Parser" in output
    assert "Can Commit" in output
    assert "generic" in output
    assert "yes" in output


def test_show_providers_compact_mode_hides_status() -> None:
    """Compact mode should hide Status column."""
    providers = ["openai", "anthropic"]

    ctx = _make_display_context_for_mode("compact")
    stream = ctx.console.file  # type: ignore[attr-defined]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    show_providers(providers, display_context=ctx)
    output = stream.getvalue()

    assert "openai" in output
    assert "anthropic" in output
    assert "Status" not in output
    # Note: "Available" appears in the title "Available Providers", so we don't check it


def test_show_providers_wide_mode_shows_status() -> None:
    """Wide mode should show Status column."""
    providers = ["openai", "anthropic"]

    ctx = _make_display_context_for_mode("wide")
    stream = ctx.console.file  # type: ignore[attr-defined]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    show_providers(providers, display_context=ctx)
    output = stream.getvalue()

    assert "openai" in output
    assert "anthropic" in output
    assert "Status" in output
    assert "Available" in output


def test_show_checkpoint_summary_compact_mode_hides_review_pass() -> None:
    """Compact mode should hide Review Pass row."""
    options = CheckpointSummaryOptions(
        phase="review",
        iteration=2,
        total_iterations=7,
        reviewer_pass=1,
        total_reviewer_passes=3,
    )

    ctx = _make_display_context_for_mode("compact")
    stream = ctx.console.file  # type: ignore[attr-defined]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    show_checkpoint_summary(options, display_context=ctx)
    output = stream.getvalue()

    assert "Phase" in output
    assert "review" in output
    assert "Iteration" in output
    assert "2/7" in output
    assert "Review Pass" not in output


def test_show_checkpoint_summary_wide_mode_shows_review_pass() -> None:
    """Wide mode should show Review Pass row."""
    options = CheckpointSummaryOptions(
        phase="review",
        iteration=2,
        total_iterations=7,
        reviewer_pass=1,
        total_reviewer_passes=3,
    )

    ctx = _make_display_context_for_mode("wide")
    stream = ctx.console.file  # type: ignore[attr-defined]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    show_checkpoint_summary(options, display_context=ctx)
    output = stream.getvalue()

    assert "Phase" in output
    assert "review" in output
    assert "Iteration" in output
    assert "2/7" in output
    assert "Review Pass" in output
    assert "1/3" in output


# --- Compact mode overflow and config panel width tests ---


def _make_compact_context(width: int = 40) -> tuple[object, io.StringIO]:
    """Create a compact DisplayContext with a fixed narrow width."""
    stream = io.StringIO()
    console = Console(
        file=stream,
        color_system=None,
        force_terminal=False,
        theme=RALPH_THEME,
        width=width,
    )
    ctx = make_display_context(console=console, env={"COLUMNS": str(width)})
    return ctx, stream


def test_show_agents_compact_narrow_title_present() -> None:
    """Compact mode still renders the Configured Agents title."""
    agent = AgentConfig(cmd="/usr/bin/ralph", json_parser=JsonParserType.GENERIC)
    config = UnifiedConfig(agents={"myagent": agent})
    ctx, stream = _make_compact_context(40)
    show_agents(config, display_context=ctx)
    output = stream.getvalue()
    assert "Configured Agents" in output
    assert "myagent" in output


def test_show_config_compact_shows_effective_configuration_title() -> None:
    """Compact show_config wraps JSON in a Panel with the title visible."""
    ctx, stream = _make_compact_context(40)
    show_config(UnifiedConfig(), display_context=ctx)
    output = stream.getvalue()
    assert "Effective Configuration" in output


def test_show_providers_compact_narrow_shows_providers() -> None:
    """Compact mode narrow terminal still shows provider names."""
    ctx, stream = _make_compact_context(40)
    show_providers(["openai"], display_context=ctx)
    output = stream.getvalue()
    assert "openai" in output
