from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke.completion_quiesce import (
    CompletionQuiesce,
    CompletionQuiesceDecision,
    clear_partial_child_edits,
    partial_child_edits,
    record_partial_child_edits,
)
from ralph.agents.invoke.opencode_subagent_sessions import (
    OpenCodeChildPart,
    OpenCodeSubagentSessionProbe,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(5)


def test_completion_waits_for_working_children_then_tears_down_when_they_finish() -> None:
    quiesce = CompletionQuiesce(window_seconds=300.0)

    assert quiesce.decide(("U4", "U7"), now=10.0) is CompletionQuiesceDecision.WAIT
    assert quiesce.decide(("U4",), now=200.0) is CompletionQuiesceDecision.WAIT
    assert quiesce.decide((), now=250.0) is CompletionQuiesceDecision.TEARDOWN


def test_children_still_working_at_the_deadline_are_torn_down_as_partial() -> None:
    quiesce = CompletionQuiesce(window_seconds=300.0)

    assert quiesce.decide(("U8",), now=0.0) is CompletionQuiesceDecision.WAIT
    assert quiesce.decide(("U8",), now=300.0) is CompletionQuiesceDecision.TEARDOWN_PARTIAL


def test_no_children_tears_down_at_once() -> None:
    assert CompletionQuiesce(window_seconds=300.0).decide((), now=0.0) is (
        CompletionQuiesceDecision.TEARDOWN
    )


def test_partial_child_edits_are_recorded_for_the_commit_reviewer(tmp_path: Path) -> None:
    record_partial_child_edits(tmp_path, agent="opencode/glm", children=("U4", "U7"))

    assert partial_child_edits(tmp_path) == ("U4", "U7")
    clear_partial_child_edits(tmp_path)
    assert partial_child_edits(tmp_path) == ()


class _Parts:
    def __init__(self) -> None:
        self.parts: list[OpenCodeChildPart] = []

    def fetch(self, _parent: str, _since_ms: int) -> list[OpenCodeChildPart]:
        return list(self.parts)

    def close(self) -> None:
        return None


def _part(child: str, updated_ms: int) -> OpenCodeChildPart:
    return OpenCodeChildPart(
        part_id=f"{child}-{updated_ms}",
        child_session_id=child,
        agent="fixer",
        title=child,
        kind="text",
        time_updated_ms=updated_ms,
    )


def test_the_probe_reports_children_that_worked_recently() -> None:
    source = _Parts()
    clock = [0.0]
    probe = OpenCodeSubagentSessionProbe(
        source=source,
        parent_session_id=lambda: "parent",
        subagent_sink=lambda _summary: None,
        child_progress_sink=lambda _child: None,
        monotonic=lambda: clock[0],
        wall_clock_ms=lambda: 0,
        poll_interval_seconds=0.0,
    )
    source.parts = [_part("U4", 1), _part("U7", 2)]
    probe.poll()
    clock[0] = 100.0
    source.parts = [_part("U7", 3)]
    probe.poll()

    assert probe.recently_active_children(within_seconds=60.0) == ("U7",)
    assert set(probe.recently_active_children(within_seconds=200.0)) == {"U4", "U7"}
