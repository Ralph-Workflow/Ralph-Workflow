from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.agent_config import AgentConfig
from ralph.config.models import UnifiedConfig
from ralph.pipeline.attempt_runner import (
    fail_open_dead_agent_probe_runner,
    seamed_dead_agent_probe_runner,
)
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    PROBE_COULD_NOT_RUN_DETAIL,
    ProbeOutcome,
    ProbeResult,
)
from ralph.recovery.dead_agent_probe import PROBE_REASON_PROBE_NOT_CONFIGURED

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_injected_string_stream_reaches_the_probe_unchanged": (
        "seamed_dead_agent_probe_runner widened to return the richer ProbeResult carrier; "
        "assertion checks result.outcome == ProbeOutcome.PASSED"
    ),
    "test_injected_stream_with_non_str_item_fails_at_consumption_time": (
        "seamed_dead_agent_probe_runner widened to return the richer ProbeResult carrier; "
        "assertion checks result.outcome == ProbeOutcome.INCONCLUSIVE"
    ),
    "test_the_fail_open_probe_runner_reports_that_no_probe_is_configured": (
        "new coverage: the not-configured fail-open route returns an inconclusive probe "
        "result carrying the probe_not_configured reason instead of a bare None, and the "
        "assertion pins that carrier"
    ),
}

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.agents.invoke import InvokeOptions

_AGENT = "fake-agent"


def _config() -> UnifiedConfig:
    return UnifiedConfig(agents={_AGENT: AgentConfig(cmd="fake-agent", model="fake-model")})


def _runner_for(
    items: Iterable[object],
    consumed: list[object],
    workspaces: list[Path],
    *,
    write_marker: bool,
) -> Callable[[str, str], ProbeResult | None]:
    def injected(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterable[object]:
        del config, prompt_file

        def stream() -> Iterable[object]:
            for item in items:
                consumed.append(item)
                yield item
            if write_marker:
                assert options is not None
                workspace_path = options.workspace_path
                assert workspace_path is not None
                workspaces.append(workspace_path)
                (workspace_path / CANARY_MARKER_FILENAME).write_text(
                    CANARY_MARKER_CONTENT, encoding="utf-8"
                )

        return stream()

    runner = seamed_dead_agent_probe_runner(_config(), injected)
    assert runner is not None
    return runner


def test_injected_string_stream_reaches_the_probe_unchanged(tmp_path: Path) -> None:
    consumed: list[object] = []
    workspaces: list[Path] = []
    items = ["first line", "second line", "third line"]
    runner = _runner_for(items, consumed, workspaces, write_marker=True)

    result = runner("canary_probe", _AGENT)

    assert result is not None
    assert result.outcome == ProbeOutcome.PASSED
    assert consumed == items
    assert len(workspaces) == 1
    assert workspaces[0] != tmp_path
    assert not list(tmp_path.iterdir())


def test_injected_stream_with_non_str_item_fails_at_consumption_time(
    tmp_path: Path,
) -> None:
    consumed: list[object] = []
    workspaces: list[Path] = []
    items: list[object] = ["first line", 42]
    runner = _runner_for(items, consumed, workspaces, write_marker=False)

    result = runner("canary_probe", _AGENT)

    assert result is not None
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert consumed == items
    assert workspaces == []
    assert not list(tmp_path.iterdir())


def test_the_fail_open_probe_runner_reports_that_no_probe_is_configured() -> None:
    runner = fail_open_dead_agent_probe_runner()

    result = runner("canary_probe", _AGENT)

    assert result is not None
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == PROBE_COULD_NOT_RUN_DETAIL
    assert result.reason == PROBE_REASON_PROBE_NOT_CONFIGURED
