from __future__ import annotations

from collections.abc import Mapping

from ralph.mcp.explore.bench import (
    REQUIRED_FIXTURES,
    BenchmarkCounters,
    BenchmarkResult,
    ScriptedCall,
    SystemClock,
    _fixed_token_count,
    derive_visible_catalog,
    run_benchmark,
    tool_catalog_tokens,
)


class _CountingExecutor:
    def __init__(self) -> None:
        self.invocations: list[ScriptedCall] = []

    def __call__(self, call: ScriptedCall) -> Mapping[str, object]:
        self.invocations.append(call)
        return _indexed_executor(call)


class FakeClock:
    def __init__(self, initial: float = 0.0, step: float = 0.1) -> None:
        self._t = initial
        self._step = step

    def now(self) -> float:
        self._t += self._step
        return self._t


def _baseline_executor(_call: ScriptedCall) -> Mapping[str, object]:
    return {
        "text": "x" * 512,
        "truncated": False,
        "index_used": False,
        "is_stale": False,
    }


def _indexed_executor(call: ScriptedCall) -> Mapping[str, object]:
    ids = list(call.expected_evidence_ids) or ["ev:placeholder"]
    return {
        "text": "x" * 32,
        "evidence_id": ids[0] if ids else "ev:placeholder",
        "evidence_ids": ids,
        "index_used": True,
        "is_stale": False,
    }


def test_required_fixtures_has_three_questions() -> None:
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


def test_tool_catalog_tokens_estimates_tool_definitions_with_schemas() -> None:

    class _ToolSpec:
        def __init__(self, name: str, description: str, input_schema: dict) -> None:
            self.name = name
            self.description = description
            self.input_schema = input_schema

    read_file = _ToolSpec(
        "read_file",
        "Read a UTF-8 file from the workspace.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    tokens = tool_catalog_tokens([read_file])
    assert set(tokens) == {"read_file"}
    assert tokens["read_file"] > len(read_file.description.split())


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
    expected_per_script = 0.5
    assert abs(result.baseline.wall_time_seconds - expected_per_script) < 1e-9
    assert abs(result.indexed.wall_time_seconds - expected_per_script) < 1e-9
    assert result.indexed.wall_time_seconds == result.baseline.wall_time_seconds


def test_benchmark_counters_is_immutable() -> None:
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


def test_run_benchmark_derives_catalog_tokens_from_visible_tool_catalog() -> None:
    from ralph.mcp.explore.bench import BenchmarkFixture

    fixture = BenchmarkFixture(
        question_id="derive-catalog",
        description="derive-catalog fixture",
        workspace_files={"a.py": "x = 1\n"},
        baseline_script=(ScriptedCall(tool="search_files", params={"pattern": "x"}),),
        indexed_script=(ScriptedCall(tool="search_files", params={"pattern": "x"}),),
        expected_evidence_ids=("ev:derive",),
        max_returned_bytes=2048,
        max_tool_calls=1,
    )
    visible_catalog = (
        ("search_files", "Find files matching a glob pattern."),
        ("grep_files", "Find text matching a regex across the workspace."),
    )
    expected_catalog_tokens = sum(len(desc.split()) for _name, desc in visible_catalog)
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=_indexed_executor,
        clock=FakeClock(),
        visible_tool_catalog=visible_catalog,
    )
    assert result.baseline.transcript_tokens >= expected_catalog_tokens
    assert result.indexed.transcript_tokens >= expected_catalog_tokens


def test_run_benchmark_invokes_each_executor_exactly_once_per_scripted_call() -> None:
    baseline = _CountingExecutor()
    indexed = _CountingExecutor()
    fixture = REQUIRED_FIXTURES[0]
    run_benchmark(
        fixture,
        baseline_executor=baseline,
        indexed_executor=indexed,
        clock=FakeClock(),
    )
    assert len(baseline.invocations) == len(fixture.baseline_script)
    assert len(indexed.invocations) == len(fixture.indexed_script)
    run_benchmark(
        fixture,
        baseline_executor=baseline,
        indexed_executor=indexed,
        clock=FakeClock(),
    )
    assert len(baseline.invocations) == 2 * len(fixture.baseline_script)
    assert len(indexed.invocations) == 2 * len(fixture.indexed_script)


def test_run_benchmark_collects_evidence_ids_without_replay() -> None:
    counter = _CountingExecutor()
    fixture = REQUIRED_FIXTURES[0]
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=counter,
        clock=FakeClock(),
    )
    assert len(counter.invocations) == len(fixture.indexed_script)
    assert result.indexed.evidence_recall == 1.0
    assert result.indexed.tool_calls == len(fixture.indexed_script)


def test_run_benchmark_transcript_counts_full_catalog_and_evidence_context() -> None:
    fixture = REQUIRED_FIXTURES[0]
    catalog_tokens = 50
    final_evidence_tokens = 16
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=_indexed_executor,
        clock=FakeClock(),
        catalog_tokens=catalog_tokens,
        final_evidence_tokens=final_evidence_tokens,
    )
    assert result.baseline.transcript_tokens >= catalog_tokens
    assert result.baseline.transcript_tokens >= final_evidence_tokens
    assert result.indexed.transcript_tokens >= catalog_tokens
    assert result.indexed.transcript_tokens >= final_evidence_tokens


def test_derive_visible_catalog_reads_registered_tool_specs() -> None:
    from ralph.mcp.tools.names import RalphToolName

    catalog = derive_visible_catalog()
    assert isinstance(catalog, tuple)
    names = {name for name, _description in catalog}
    expected_names = {member.value for member in RalphToolName}
    assert names == expected_names, (
        "derive_visible_catalog must return the registered Ralph-owned "
        "tool specs. A missing tool silently drops catalog_tokens for "
        "the gate, and an extra tool overstates the full-transcript "
        "counter."
    )
    for name, definition in catalog:
        assert isinstance(name, str) and name, f"{name!r} must be a non-empty string"
        assert getattr(definition, "input_schema", None) is not None, (
            f"{name!r} must carry an input schema"
        )


def test_derive_visible_catalog_is_deterministic_across_calls() -> None:
    first = derive_visible_catalog()
    second = derive_visible_catalog()
    assert first == second
    from ralph.mcp.tools.names import RalphToolName

    assert len(first) == len(RalphToolName)


def test_run_benchmark_default_catalog_matches_registered_specs() -> None:
    from ralph.mcp.explore.bench import tool_catalog_tokens

    derived_catalog = derive_visible_catalog()
    derived_tokens = tool_catalog_tokens(derived_catalog)
    expected_min = sum(
        len(str(definition.input_schema).split()) for _name, definition in derived_catalog
    )
    assert sum(derived_tokens.values()) >= expected_min
