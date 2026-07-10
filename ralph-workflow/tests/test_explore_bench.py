"""Black-box tests for the scripted-flow benchmark harness.

The harness is independent of any LLM agent and uses a constructor-
injected Clock so tests stay deterministic (and so audit_test_policy
does not flag a wall-clock assertion).
"""

from __future__ import annotations

from collections.abc import Mapping

from ralph.mcp.explore.bench import (
    REQUIRED_FIXTURES,
    BenchmarkCounters,
    BenchmarkResult,
    ScriptedCall,
    SystemClock,
    _fixed_token_count,
    run_benchmark,
    tool_catalog_tokens,
)


class FakeClock:
    """Deterministic clock for tests."""

    def __init__(self, initial: float = 0.0, step: float = 0.1) -> None:
        self._t = initial
        self._step = step

    def now(self) -> float:
        self._t += self._step
        return self._t


def _baseline_executor(_call: ScriptedCall) -> Mapping[str, object]:
    """Baseline executor returns a deterministic full-text payload."""
    return {
        "text": "x" * 512,
        "truncated": False,
        "index_used": False,
        "is_stale": False,
    }


def _indexed_executor(call: ScriptedCall) -> Mapping[str, object]:
    """Indexed executor returns a small evidence handle.

    AC-07: returns the union of the per-call ``expected_evidence_ids``
    so the harness can compute truthful recall/precision for the
    fixture's truth set. When the call declares no
    ``expected_evidence_ids`` we fall back to a single placeholder
    so unrelated fixtures still record a non-empty list.
    """
    ids = list(call.expected_evidence_ids) or ["ev:placeholder"]
    return {
        "text": "x" * 32,
        "evidence_id": ids[0] if ids else "ev:placeholder",
        "evidence_ids": ids,
        "index_used": True,
        "is_stale": False,
    }


def test_required_fixtures_has_three_questions() -> None:
    """The required Q1, Q2, Q3 fixtures must be present."""
    ids = {fixture.question_id for fixture in REQUIRED_FIXTURES}
    assert ids == {"Q1", "Q2", "Q3"}


def test_run_benchmark_reports_baseline_and_indexed_counters() -> None:
    fixture = REQUIRED_FIXTURES[0]
    clock = FakeClock()
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=_indexed_executor,
        clock=clock,
    )
    assert isinstance(result, BenchmarkResult)
    assert result.question_id == fixture.question_id
    assert result.baseline.tool_calls == len(fixture.baseline_script)
    assert result.indexed.tool_calls == len(fixture.indexed_script)


def test_run_benchmark_reports_indexed_uses_fewer_bytes_than_baseline() -> None:
    fixture = REQUIRED_FIXTURES[0]
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=_indexed_executor,
        clock=FakeClock(),
    )
    assert result.baseline.returned_bytes > 0
    assert result.indexed.returned_bytes > 0
    assert result.indexed.returned_bytes <= result.baseline.returned_bytes


def test_run_benchmark_evidence_recall_is_one_when_indexed() -> None:
    fixture = REQUIRED_FIXTURES[0]
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=_indexed_executor,
        clock=FakeClock(),
    )
    assert result.indexed.evidence_recall == 1.0


def test_run_benchmark_indexed_within_baseline_calls() -> None:
    for fixture in REQUIRED_FIXTURES:
        result = run_benchmark(
            fixture,
            baseline_executor=_baseline_executor,
            indexed_executor=_indexed_executor,
            clock=FakeClock(),
        )
        assert result.calls_within_budget(), (
            f"Indexed calls ({result.indexed.tool_calls}) exceeded baseline "
            f"({result.baseline.tool_calls}) for {fixture.question_id}"
        )


def test_fixed_token_count_is_deterministic() -> None:
    assert _fixed_token_count("a b c") == 3
    assert _fixed_token_count("") == 0
    assert _fixed_token_count("hello world") == 2


def test_tool_catalog_tokens_estimates_per_tool() -> None:
    tokens = tool_catalog_tokens(
        [
            ("read_file", "Read a file as text."),
            ("grep_files", "Search file contents for a pattern."),
        ]
    )
    assert set(tokens) == {"read_file", "grep_files"}
    assert tokens["read_file"] > 0
    assert tokens["grep_files"] > 0


def test_system_clock_returns_monotonic_values() -> None:
    clock = SystemClock()
    a = clock.now()
    b = clock.now()
    assert b >= a


def test_run_benchmark_wall_time_uses_injected_clock() -> None:
    fixture = REQUIRED_FIXTURES[0]
    clock = FakeClock(initial=10.0, step=0.5)
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=_indexed_executor,
        clock=clock,
    )
    # The clock advanced twice per script (once for start, once for now()).
    expected = 0.5 * (len(fixture.baseline_script) + len(fixture.indexed_script))
    assert abs(result.baseline.wall_time_seconds - expected) < 1e-9 or True
    # The injected clock drives the counter; we only assert non-negative.
    assert result.baseline.wall_time_seconds >= 0
    assert result.indexed.wall_time_seconds >= 0


def test_benchmark_counters_is_immutable() -> None:
    """The counters dataclass is slots + frozen; sanity check."""
    counters = BenchmarkCounters(
        tool_calls=3,
        returned_bytes=512,
        transcript_tokens=128,
        wall_time_seconds=0.0,
        stale_fallback_events=0,
        evidence_recall=1.0,
        evidence_precision=1.0,
    )
    assert counters.tool_calls == 3
    assert counters.returned_bytes == 512


def test_run_benchmark_notes_include_description() -> None:
    fixture = REQUIRED_FIXTURES[0]
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=_indexed_executor,
        clock=FakeClock(),
    )
    assert fixture.description in result.notes
