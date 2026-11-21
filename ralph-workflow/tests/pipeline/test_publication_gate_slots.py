from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.pipeline.publication_gate import (
    PublicationGateRun,
    check_publication_gate,
    publication_gate_feedback,
)
from ralph.pipeline.publication_gate_slots import (
    GATE_SLOTS_ENV,
    configured_gate_slots,
    publication_gate_slot,
)
from ralph.policy.loader import cached_default_policy_bundle

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(10)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_gate_that_ran_out_of_time_is_inconclusive": (
        "operator directive: a gate run whose only failure markers are time or budget markers "
        "is time-only and lands with a warning instead of re-running inconclusively; renamed "
        "to test_a_gate_that_ran_out_of_time_lands_as_time_only"
    ),
}


_TREE = "a" * 40


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += seconds
        return False


def test_gate_slots_are_shared_and_bounded(tmp_path: Path) -> None:
    clock = _Clock()
    with publication_gate_slot(slots=1, directory=tmp_path) as first:
        assert first is True
        logs: list[str] = []
        sink = logger.add(lambda message: logs.append(str(message)), level="INFO")
        try:
            with publication_gate_slot(
                slots=1,
                directory=tmp_path,
                wait_seconds=30.0,
                monotonic=clock.monotonic,
                wait=clock.wait,
            ) as second:
                assert second is False
        finally:
            logger.remove(sink)
    assert clock.now >= 30.0
    assert len([line for line in logs if "waiting for a publication gate slot" in line]) == 1
    with publication_gate_slot(slots=1, directory=tmp_path) as again:
        assert again is True


def test_two_slots_admit_two_gates_at_once(tmp_path: Path) -> None:
    with (
        publication_gate_slot(slots=2, directory=tmp_path) as first,
        publication_gate_slot(slots=2, directory=tmp_path) as second,
    ):
        assert first is True
        assert second is True


def test_the_slot_count_is_configurable() -> None:
    assert configured_gate_slots({GATE_SLOTS_ENV: "3"}) == 3
    assert configured_gate_slots({GATE_SLOTS_ENV: "0"}) >= 1
    assert configured_gate_slots({GATE_SLOTS_ENV: "many"}) >= 1
    assert configured_gate_slots({}) >= 1


def _declare(root: Path) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("RALPH-FACT: authoritative_verify_command: `make verify`\n", encoding="utf-8")


def _tree(_root: Path, _commit: str) -> str:
    return _TREE


@pytest.mark.parametrize(
    "output",
    [
        "[DEADLINE REACHED] Monotonic budget deadline of 523s exceeded!\n",
        "stage lint: deadline exceeded after 600 s\n",
        "`make verify` failed while running: make test (budget exhausted)\n",
        "Combined test budget exhausted: wall 400s / 75s\n",
    ],
)
def test_a_gate_that_ran_out_of_time_lands_as_time_only(tmp_path: Path, output: str) -> None:
    _declare(tmp_path)
    policy = cached_default_policy_bundle().pipeline
    calls: list[str] = []

    def runner(command: str, _cwd: Path, _timeout: float) -> PublicationGateRun:
        calls.append(command)
        return PublicationGateRun(exit_code=3, output=f"visual tests ok\n{output}")

    first = check_publication_gate(tmp_path, "HEAD", runner=runner, tree_of=_tree)
    check_publication_gate(tmp_path, "HEAD", runner=runner, tree_of=_tree)

    assert first is not None
    assert first.time_only is True
    assert first.block is None
    assert first.routes_back_to_development is False
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is None
    assert len(calls) == 1


def test_a_red_gate_without_a_time_marker_still_routes_back(tmp_path: Path) -> None:
    _declare(tmp_path)

    def runner(_command: str, _cwd: Path, _timeout: float) -> PublicationGateRun:
        return PublicationGateRun(exit_code=1, output="[verify] FAILED: rspec shard 2\n")

    result = check_publication_gate(tmp_path, "HEAD", runner=runner, tree_of=_tree)

    assert result is not None
    assert result.inconclusive is False
    assert result.routes_back_to_development is True
