from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.agent_config import AgentConfig
from ralph.config.models import UnifiedConfig
from ralph.pipeline.attempt_runner import seamed_dead_agent_probe_runner
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    ProbeOutcome,
)

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
) -> Callable[[str, str], ProbeOutcome | None]:
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

    outcome = runner("canary_probe", _AGENT)

    assert outcome == ProbeOutcome.PASSED
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

    outcome = runner("canary_probe", _AGENT)

    assert outcome == ProbeOutcome.INCONCLUSIVE
    assert consumed == items
    assert workspaces == []
    assert not list(tmp_path.iterdir())
