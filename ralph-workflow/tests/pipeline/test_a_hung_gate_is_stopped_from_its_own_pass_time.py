from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.publication_gate import (
    PUBLICATION_GATE_TIMEOUT_SECONDS,
    check_publication_gate,
)
from ralph.pipeline.publication_gate_run import PublicationGateRun

if TYPE_CHECKING:
    from pathlib import Path


def _declare(root: Path) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True)
    policy.write_text("RALPH-FACT: authoritative_verify_command: `make verify`\n", encoding="utf-8")


class _Gate:
    def __init__(self, *runs: PublicationGateRun) -> None:
        self._runs = list(runs)
        self.timeouts: list[float] = []

    def __call__(self, _command: str, _root: Path, timeout_seconds: float) -> PublicationGateRun:
        self.timeouts.append(timeout_seconds)
        return self._runs.pop(0)


def _trees(*shas: str) -> object:
    queue = list(shas)
    return lambda _root, _commit: queue.pop(0)


def test_a_gate_with_no_passing_history_gets_the_outer_bound(tmp_path: Path) -> None:
    _declare(tmp_path)
    gate = _Gate(PublicationGateRun(exit_code=0, output="ok", elapsed_seconds=26.0))
    check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees("t1"))
    assert gate.timeouts == [PUBLICATION_GATE_TIMEOUT_SECONDS]


def test_a_fast_gate_is_stopped_minutes_not_half_an_hour_after_it_hangs(tmp_path: Path) -> None:
    _declare(tmp_path)
    gate = _Gate(
        PublicationGateRun(exit_code=0, output="ok", elapsed_seconds=26.0),
        PublicationGateRun(exit_code=124, output="gate test-rs took", timed_out=True),
    )
    check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees("t1"))
    result = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees("t2"))
    assert gate.timeouts[1] == 300.0
    assert result is not None
    assert result.routes_back_to_development
    assert "hung" in result.feedback()


def test_a_slow_gate_keeps_ten_times_its_pass_time(tmp_path: Path) -> None:
    _declare(tmp_path)
    gate = _Gate(
        PublicationGateRun(exit_code=0, output="ok", elapsed_seconds=90.0),
        PublicationGateRun(exit_code=0, output="ok", elapsed_seconds=95.0),
    )
    check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees("t1"))
    check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees("t2"))
    assert gate.timeouts[1] == 900.0


def test_a_failing_run_keeps_the_last_passing_time(tmp_path: Path) -> None:
    _declare(tmp_path)
    gate = _Gate(
        PublicationGateRun(exit_code=0, output="ok", elapsed_seconds=40.0),
        PublicationGateRun(exit_code=2, output="FAILED tests/x.py::t", elapsed_seconds=2.0),
        PublicationGateRun(exit_code=0, output="ok", elapsed_seconds=41.0),
    )
    for tree in ("t1", "t2", "t3"):
        check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(tree))
    assert gate.timeouts[2] == 400.0
