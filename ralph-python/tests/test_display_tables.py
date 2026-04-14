from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from collections.abc import Callable

# Keep Path available for annotations referenced by the models under test.
_ = Path

GeneralConfig.model_rebuild()
UnifiedConfig.model_rebuild()


def _capture_output(func: Callable[..., None], *args: Any, **kwargs: Any) -> str:
    stream = io.StringIO()
    console = Console(file=stream, color_system=None, force_terminal=False)
    kwargs.setdefault("console", console)
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
