from __future__ import annotations

import json
from pathlib import Path

import pytest
from loguru import logger

from ralph.pipeline.publication_gate import (
    MAX_INCONCLUSIVE_GATE_RUNS,
    PublicationGateRun,
    blocked_publication,
    check_publication_gate,
    publication_gate_feedback,
    publication_gate_record_path,
)
from ralph.policy.loader import cached_default_policy_bundle

pytestmark = pytest.mark.timeout_seconds(10)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "publication_gate"
_TREE = "d" * 40


def _declare(root: Path) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("RALPH-FACT: authoritative_verify_command: `bin/verify`\n", encoding="utf-8")


def _tree(_root: Path, _commit: str) -> str:
    return _TREE


class _Gate:
    def __init__(self, exit_code: int, output: str) -> None:
        self.exit_code = exit_code
        self.output = output
        self.calls = 0

    def __call__(self, _command: str, _cwd: Path, _timeout: float) -> PublicationGateRun:
        self.calls += 1
        return PublicationGateRun(exit_code=self.exit_code, output=self.output)


def _record(root: Path) -> dict[str, object]:
    raw: dict[str, object] = json.loads(
        publication_gate_record_path(root).read_text(encoding="utf-8")
    )
    return raw


@pytest.mark.parametrize(
    ("fixture", "exit_code", "marker"),
    [
        pytest.param("deadline_exhausted.txt", 3, "deadline exhausted", id="deadline-exhausted"),
        pytest.param("duration_guard.txt", 5, "duration guard failed", id="duration-guard"),
    ],
)
def test_a_time_only_gate_failure_lands_with_one_warning(
    tmp_path: Path, fixture: str, exit_code: int, marker: str
) -> None:
    _declare(tmp_path)
    policy = cached_default_policy_bundle().pipeline
    gate = _Gate(exit_code, (_FIXTURES / fixture).read_text(encoding="utf-8"))
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        result = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_tree)
        again = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_tree)
    finally:
        logger.remove(sink)

    assert result is not None
    assert result.time_only is True
    assert result.block is None
    assert result.routes_back_to_development is False
    assert again is not None
    assert again.time_only is True
    assert gate.calls == 1
    assert _record(tmp_path)["time_only"] is True
    assert blocked_publication(tmp_path, "HEAD", tree_of=_tree) is None
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is None
    time_warnings = [line for line in warnings if "time-only" in line]
    assert len(time_warnings) == 1
    assert marker in time_warnings[0].lower()
    assert _TREE in time_warnings[0]


@pytest.mark.parametrize(
    "failure",
    [
        "FAILED tests/test_app.py::test_total - AssertionError\n",
        "[verify] FAILED: rspec shard 2\n",
        "Finished in 3.2 seconds\n12 examples, 2 failures\n",
        "app.py:3: error: Incompatible types in assignment\nFound 1 error in 1 file\n",
    ],
)
def test_a_real_failure_beside_a_deadline_line_still_routes_back(
    tmp_path: Path, failure: str
) -> None:
    _declare(tmp_path)
    policy = cached_default_policy_bundle().pipeline
    output = f"{failure}[DEADLINE] Verification deadline exhausted (525s budget)\n"

    result = check_publication_gate(tmp_path, "HEAD", runner=_Gate(1, output), tree_of=_tree)

    assert result is not None
    assert result.time_only is False
    assert result.inconclusive is False
    assert result.block is not None
    assert result.routes_back_to_development is True
    assert _record(tmp_path)["time_only"] is False
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is not None


def test_an_exhausted_inconclusive_budget_lands_as_time_only(tmp_path: Path) -> None:
    _declare(tmp_path)
    gate = _Gate(-15, "killed\n")
    held: list[bool] = []
    time_only: list[bool] = []

    for _run in range(MAX_INCONCLUSIVE_GATE_RUNS + 2):
        result = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_tree)
        assert result is not None
        time_only.append(result.time_only and result.block is None)
        held.append(blocked_publication(tmp_path, "HEAD", tree_of=_tree) is not None)

    assert gate.calls == MAX_INCONCLUSIVE_GATE_RUNS
    assert time_only == [False] * (MAX_INCONCLUSIVE_GATE_RUNS - 1) + [True] * 3
    assert held == [False] * (MAX_INCONCLUSIVE_GATE_RUNS + 2)
    assert _record(tmp_path)["time_only"] is True


_HEARTBEAT = "# [visual-harness:heartbeat] test in progress...\n" * 200


def test_a_failure_far_before_the_output_tail_is_kept_and_routes_back(tmp_path: Path) -> None:
    _declare(tmp_path)
    policy = cached_default_policy_bundle().pipeline
    output = f"[verify] FAILED: rspec shard 2\n{_HEARTBEAT}"

    result = check_publication_gate(tmp_path, "HEAD", runner=_Gate(5, output), tree_of=_tree)
    event = publication_gate_feedback(tmp_path, policy, "development_commit")

    assert result is not None
    assert "[verify] FAILED: rspec shard 2" not in result.output_tail
    assert result.time_only is False
    assert result.inconclusive is False
    assert result.routes_back_to_development is True
    assert "[verify] FAILED: rspec shard 2" in str(_record(tmp_path)["failure_digest"])
    assert event is not None
    summary = event.detail.index("Failure summary:")
    assert summary < event.detail.index("[verify] FAILED: rspec shard 2")
    assert event.detail.index("[verify] FAILED: rspec shard 2") < event.detail.index("Output tail:")


def test_a_deadline_marker_far_before_the_output_tail_is_still_time_only(
    tmp_path: Path,
) -> None:
    _declare(tmp_path)
    output = f"[DEADLINE] Verification deadline exhausted (525s budget)\n{_HEARTBEAT}"

    result = check_publication_gate(tmp_path, "HEAD", runner=_Gate(3, output), tree_of=_tree)

    assert result is not None
    assert result.time_only is True
    assert result.block is None
    assert blocked_publication(tmp_path, "HEAD", tree_of=_tree) is None


def test_a_red_gate_without_failure_lines_routes_back_and_says_so(tmp_path: Path) -> None:
    _declare(tmp_path)
    policy = cached_default_policy_bundle().pipeline

    result = check_publication_gate(tmp_path, "HEAD", runner=_Gate(5, _HEARTBEAT), tree_of=_tree)
    event = publication_gate_feedback(tmp_path, policy, "development_commit")

    assert result is not None
    assert result.time_only is False
    assert result.routes_back_to_development is True
    assert event is not None
    assert "No failure lines were found" in event.detail
    assert "exit 5" in event.detail
    assert "heartbeat" in event.detail


def test_a_record_saved_without_a_failure_digest_still_loads(tmp_path: Path) -> None:
    _declare(tmp_path)
    policy = cached_default_policy_bundle().pipeline
    publication_gate_record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    publication_gate_record_path(tmp_path).write_text(
        json.dumps(
            {
                "command": "bin/verify",
                "tree_sha": _TREE,
                "exit_code": 1,
                "timed_out": False,
                "output_tail": "[verify] FAILED: unit tests\n",
                "failure_signature": "0123456789abcdef",
            }
        ),
        encoding="utf-8",
    )
    gate = _Gate(1, "[verify] FAILED: unit tests\n")

    result = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_tree)
    event = publication_gate_feedback(tmp_path, policy, "development_commit")

    assert gate.calls == 0
    assert result is not None
    assert result.routes_back_to_development is True
    assert event is not None
    assert "[verify] FAILED: unit tests" in event.detail
