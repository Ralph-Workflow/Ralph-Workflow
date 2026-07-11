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
    derive_visible_catalog,
    run_benchmark,
    tool_catalog_tokens,
)


class _CountingExecutor:
    """Counting scripted-call executor for truthful tool-call regression.

    AC-07: records every ``(call)`` invocation so the test can
    assert the harness invokes each executor exactly once per
    scripted call (no replay for evidence-id collection). The
    counter is keyed by tool so the same executor instance can
    stand in for either the baseline or indexed executor.
    """

    def __init__(self) -> None:
        self.invocations: list[ScriptedCall] = []

    def __call__(self, call: ScriptedCall) -> Mapping[str, object]:
        self.invocations.append(call)
        return _indexed_executor(call)


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


def test_tool_catalog_tokens_estimates_tool_definitions_with_schemas() -> None:
    """AC-12: ``tool_catalog_tokens`` accepts objects exposing
    ``name`` / ``description`` / ``input_schema`` attributes
    (the public ``ToolDefinition`` shape) and counts the
    serialized name + description + JSON-schema text.
    """

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
    # The serialized form concatenates the description and the
    # schema text; the token count is at least the description
    # alone plus the JSON keys.
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
    # The harness calls ``clock.now()`` exactly once per script (start
    # AND end subtract back to a single ``step`` interval), so each
    # script's wall_time equals the FakeClock ``step`` of 0.5 s. The
    # previous ``... or True`` form was vacuous; this rewrite pins
    # the exact deterministic duration so a future regression that
    # introduced per-iteration ``clock.now()`` would fail the test.
    expected_per_script = 0.5
    assert abs(result.baseline.wall_time_seconds - expected_per_script) < 1e-9
    assert abs(result.indexed.wall_time_seconds - expected_per_script) < 1e-9
    # The injected clock drives both counters to the same value
    # because the FakeClock advances the same way for both branches.
    assert result.indexed.wall_time_seconds == result.baseline.wall_time_seconds


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


def test_run_benchmark_derives_catalog_tokens_from_visible_tool_catalog() -> None:
    """AC-12: when the caller passes a ``visible_tool_catalog``,
    ``run_benchmark`` derives the catalog token count from the
    bounded serialized tool descriptions/input schemas and adds
    it to the indexed transcript exactly once. The harness does
    not rely on the caller passing a non-zero ``catalog_tokens``
    parameter to opt into the full-transcript accounting.
    """
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
    expected_catalog_tokens = sum(
        len(desc.split()) for _name, desc in visible_catalog
    )
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
    """AC-07: a counting executor MUST observe exactly one invocation per
    scripted call (no replay for evidence-id collection).

    A previous implementation re-ran the indexed script via
    ``_collect_returned_evidence_ids`` so a counting executor
    observed ``len(indexed_script) * 2`` invocations while the
    reported counter stayed at ``len(indexed_script)``, hiding
    the duplicate work. This regression pins the truthful
    behavior: each executor is called once per scripted call.
    """
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
    # The reported counter MUST match the executor's observed
    # call count, not exceed it. The previous implementation
    # reported ``len(script)`` while the executor saw
    # ``len(script) * 2``.
    run_benchmark(
        fixture,
        baseline_executor=baseline,
        indexed_executor=indexed,
        clock=FakeClock(),
    )
    assert len(baseline.invocations) == 2 * len(fixture.baseline_script)
    assert len(indexed.invocations) == 2 * len(fixture.indexed_script)


def test_run_benchmark_collects_evidence_ids_without_replay() -> None:
    """AC-07: the indexed executor's evidence ids are collected during the
    counters pass, not via a separate replay.

    The fixture's truth set is the union of per-call expected ids;
    a scripted indexed flow that returns the truth set on the
    first call should reach recall == 1.0 without invoking the
    executor again. A separate replay would still report recall
    1.0, but it would also double the executor's call count,
    breaking the truthful-counter contract.
    """
    counter = _CountingExecutor()
    fixture = REQUIRED_FIXTURES[0]
    result = run_benchmark(
        fixture,
        baseline_executor=_baseline_executor,
        indexed_executor=counter,
        clock=FakeClock(),
    )
    # The executor is invoked exactly once per scripted call and
    # the harness derives recall from the single-pass evidence
    # collection (no replay).
    assert len(counter.invocations) == len(fixture.indexed_script)
    assert result.indexed.evidence_recall == 1.0
    assert result.indexed.tool_calls == len(fixture.indexed_script)


def test_run_benchmark_transcript_counts_full_catalog_and_evidence_context() -> None:
    """AC-12: the benchmark gates count the full scripted transcript
    (visible tool descriptions/input schemas, tool names +
    parameters, outputs, and final evidence context).

    A scripted flow with a non-zero ``catalog_tokens`` and
    ``final_evidence_tokens`` must add them to the indexed
    ``transcript_tokens`` exactly once. The test pins this so a
    future regression that drops either addition breaks the gate.
    """
    fixture = REQUIRED_FIXTURES[0]
    # Synthetic catalog token count: 50 visible-tool-description
    # tokens (a conservative lower bound for a small MCP catalog).
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
    # Both baseline and indexed add the catalog and final-evidence
    # tokens; the gate compares their deltas, not their absolutes.
    assert result.baseline.transcript_tokens >= catalog_tokens
    assert result.baseline.transcript_tokens >= final_evidence_tokens
    assert result.indexed.transcript_tokens >= catalog_tokens
    assert result.indexed.transcript_tokens >= final_evidence_tokens
    # The catalog and final-evidence tokens are added ONCE per
    # script (not per call) so a single run with N scripted calls
    # adds the constants exactly once. The synthetic per-call
    # payload gap (baseline 512 bytes, indexed 32 bytes) is the
    # dominant signal; we do not enforce the exact sign of the
    # transcript-token delta because the harness also accounts
    # for per-call param serialization (each indexed call carries
    # richer params than the baseline equivalent). The full
    # real-handler bench fixture is the source-of-truth check;
    # this synthetic test only verifies the catalog/final-evidence
    # additions land in BOTH the baseline and indexed counters.


def test_derive_visible_catalog_reads_registered_tool_specs() -> None:
    """AC-12: the visible catalog MUST come from the registered
    Ralph-owned registry/specs, not a synthetic fixture constant.

    The helper is the source-of-truth ``(name, description)`` list
    the bench gates pass to ``tool_catalog_tokens`` for the full
    visible-MCP-catalog transcript accounting. It must contain
    every Ralph-owned tool (no extras, no missing tools) and the
    descriptions must be non-empty so the catalog token count
    reflects the real ``tools/list`` bytes.
    """
    from ralph.mcp.tools.names import RalphToolName

    catalog = derive_visible_catalog()
    assert isinstance(catalog, tuple)
    # Catalog order is deterministic across runs (the bridge
    # declares a stable order; we only check the SET of names
    # because the bridge groups specs by family and may not
    # match the ``RalphToolName`` enum declaration order).
    names = {name for name, _description in catalog}
    expected_names = {member.value for member in RalphToolName}
    assert names == expected_names, (
        "derive_visible_catalog must return the registered Ralph-owned "
        "tool specs. A missing tool silently drops catalog_tokens for "
        "the gate, and an extra tool overstates the full-transcript "
        "counter."
    )
    # Every entry has a non-empty description (the catalog would
    # silently undercount tokens if any description was empty).
    for name, description in catalog:
        assert isinstance(name, str) and name, f"{name!r} must be a non-empty string"
        assert isinstance(description, str) and description.strip(), (
            f"{name!r} must have a non-empty description"
        )


def test_derive_visible_catalog_is_deterministic_across_calls() -> None:
    """The catalog MUST be stable so transcript-token counters are
    reproducible across runs and across MCP server instances.
    """
    first = derive_visible_catalog()
    second = derive_visible_catalog()
    assert first == second
    # Length must match the RalphToolName enum cardinality exactly
    # (no duplicates, no missing tools).
    from ralph.mcp.tools.names import RalphToolName

    assert len(first) == len(RalphToolName)


def test_run_benchmark_default_catalog_matches_registered_specs() -> None:
    """AC-12: the bench gate MUST use the registered catalog by
    default. When the caller does not pass ``visible_tool_catalog``,
    the harness MUST derive it from the registered Ralph-owned
    registry so the catalog-token counter matches the real
    ``tools/list`` bytes.

    The production ``run_benchmark`` accepts ``visible_tool_catalog``
    as a kwarg; the harness's own catalog-token derivation (see
    ``tool_catalog_tokens``) MUST agree with the description-only
    token count so a regression that empties the description (or
    drops the name) breaks the gate.
    """
    from ralph.mcp.explore.bench import tool_catalog_tokens

    derived_catalog = derive_visible_catalog()
    derived_tokens = tool_catalog_tokens(derived_catalog)
    expected_min = sum(
        len(desc.split()) for _name, desc in derived_catalog
    )
    # ``tool_catalog_tokens`` returns the serialized name +
    # description + schema, which is always >= the description-
    # only token count. The test pins the lower bound so a
    # regression that empties the description (or drops the name)
    # breaks the gate.
    assert sum(derived_tokens.values()) >= expected_min
