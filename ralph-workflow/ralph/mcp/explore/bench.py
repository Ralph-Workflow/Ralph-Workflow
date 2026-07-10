"""Fixed-tokenizer scripted-flow benchmark harness for the indexed exploration substrate.

This module owns the deterministic harness that proves the
research-gate counters from the architecture finding:

* ``transcript_tokens`` — token count of the full scripted transcript
  (tool descriptions + inputs + outputs + final evidence context)
  computed by a fixed, pinned tokenizer.
* ``returned_bytes`` — deterministic secondary proxy of transcript cost.
* ``tool_calls`` — number of scripted tool calls per flow.
* ``wall_time`` — wall-clock seconds for the scripted flow.
* ``stale_fallback_events`` — count of stale or fallback responses.
* ``evidence_recall`` — required_evidence / expected_required.
* ``evidence_precision`` — required_evidence / returned_evidence.
* ``parse_count`` — number of files reparsed during the reindex step.
* ``changed_file_count`` — number of files marked dirty and reindexed.
* ``index_storage_bytes`` — disk size of the SQLite index.

The harness runs scripted tool flows over ``tmp_path`` workspaces,
NOT a live agent. Wall time uses a constructor-injected ``Clock``
(defaults to a real clock; tests inject ``FakeClock`` to avoid the
``audit_test_policy`` wall-clock violation).

Required deterministic questions (per the architecture finding):

* Q1 — find where a tool is registered.
* Q2 — find likely tests for a handler.
* Q3 — estimate rename impact via lexical callers (graph impact is
  Phase 3, so Q3 uses lexical callers only; the deferral register
  records that semantic impact is deferred).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Final, Protocol

from ralph.mcp.explore._bench_fixtures import (
    ALL_FIXTURES,
    EXTENDED_FIXTURES,
    REQUIRED_BENCH_WORKFLOW_IDS,
    REQUIRED_FIXTURES,
)
from ralph.mcp.explore._bench_types import (
    BenchmarkCounters,
    BenchmarkFixture,
    BenchmarkResult,
    ScriptedCall,
)


# Ponytail: fixed tokenizer. Counts ASCII whitespace-separated tokens
# (split on Unicode whitespace). Cheap, deterministic, and not a
# dependency on a real tokenizer library. Tests assert the same value
# for the same input. The tokenizer is intentionally simple: this is
# a budget, not a fidelity claim.
def _fixed_token_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


def _serialize_tool_spec(spec: object) -> str:
    """Serialize a ToolDefinition-shaped object to a deterministic text.

    The serialized form concatenates the tool ``name``, ``description``,
    and ``input_schema`` (rendered as compact JSON) so the catalog
    token counter sees the same bytes the agent sees at the
    ``tools/list`` boundary. Unknown shapes fall back to ``str(spec)``
    so a regression in the registry does not blank the transcript.
    """
    name_obj: object = getattr(spec, "name", None)
    desc_obj: object = getattr(spec, "description", None)
    schema_obj: object = getattr(spec, "input_schema", None)
    if isinstance(name_obj, str) and isinstance(desc_obj, str):
        if schema_obj is not None:
            try:
                schema_text = json.dumps(schema_obj, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                schema_text = str(schema_obj)
        else:
            schema_text = ""
        return f"{name_obj} {desc_obj} {schema_text}".strip()
    return str(spec)


class Clock(Protocol):
    """Protocol for wall-clock injection. Tests inject a FakeClock."""

    def now(self) -> float:
        ...


class SystemClock:
    """Default wall-clock implementation backed by ``time.monotonic``."""

    def now(self) -> float:
        return time.monotonic()



def _tokenize_call(call: ScriptedCall) -> int:
    """Approximate token cost of a single tool call's description+input."""
    payload = f"{call.tool} {sorted(call.params.items())}"
    return _fixed_token_count(payload)


# Ponytail: tool description token accounting. Helper used by the
# MCP-description gate to estimate per-tool schema tokens. Stable across
# runs and deterministic.
# Minimum tuple length for legacy ``(name, description)`` entries
# that come through the catalog.
_LEGACY_TUPLE_ARITY: Final[int] = 2


def tool_catalog_tokens(
    tool_specs: Iterable[object],
) -> dict[str, int]:
    """Estimate token cost per tool catalog entry.

    Args:
        tool_specs: iterable of either ``(tool_name, description)``
            tuples (legacy API) or objects exposing ``name`` /
            ``description`` / ``input_schema`` attributes (the public
            ``ToolDefinition`` shape).

    Returns:
        Mapping from tool name to its fixed-token count (over the
        serialized name + description + JSON-schema text).
    """
    tokens: dict[str, int] = {}
    for spec in tool_specs:
        if isinstance(spec, tuple) and len(spec) >= _LEGACY_TUPLE_ARITY:
            name = str(spec[0])
            description_obj = spec[1]
            if isinstance(description_obj, str):
                description = description_obj
            else:
                # ``(name, ToolDefinition)`` form: reuse the spec
                # serializer so the catalog sees the same text.
                description = _serialize_tool_spec(description_obj)
        else:
            serialized = _serialize_tool_spec(spec)
            name_obj: object = getattr(spec, "name", None)
            name = str(name_obj) if isinstance(name_obj, str) else serialized
            description = serialized
        tokens[name] = _fixed_token_count(description)
    return tokens


def _run_script(
    script: Sequence[ScriptedCall],
    *,
    executor: Callable[[ScriptedCall], Mapping[str, object]],
    clock: Clock,
    catalog_tokens: int = 0,
    final_evidence_tokens: int = 0,
) -> tuple[BenchmarkCounters, set[str]]:
    """Run a scripted tool flow and aggregate counters + returned evidence ids.

    The executor is expected to return a dict with the result payload;
    the harness counts bytes/tokens but does not introspect the
    payload semantics. The returned evidence ids are collected
    in the SAME pass that records the counters; the harness MUST
    NOT replay the script to gather ids because replaying would
    silently undercount tool calls (a counting executor would see
    ``len(script) * 2`` invocations while the reported counter
    stays at ``len(script)``, hiding the duplicate work).

    AC-12: counters include derived recall/precision using the
    union of per-call expected evidence ids (the truth set) and
    the actual returned ids (the prediction set).

    AC-12 (full transcript): ``catalog_tokens`` (bounded serialized
    visible tool descriptions/input schemas) and
    ``final_evidence_tokens`` (the compact evidence context the
    agent keeps after the flow ends) are added once per script so
    the transcript cost matches the research gate's "full
    scripted transcript" definition. Defaults of ``0`` keep the
    harness back-compat with callers that pass only the script.
    """
    start = clock.now()
    returned_bytes = 0
    transcript_tokens = catalog_tokens
    stale_fallback = 0
    returned_evidence_ids: set[str] = set()
    for call in script:
        result = executor(call)
        # Ponytail: count the executor's actual response payload, not the
        # ``str(result)`` repr. The repr double-counts the JSON payload
        # inside the dict's ``text`` field (the value is wrapped in quotes
        # and the dict's keys/braces add overhead). Counting only the
        # ``text`` value matches the research-gate definition of
        # ``returned_bytes`` (the bytes the agent sees in the tool result)
        # and keeps the synthetic 512/32-byte fixtures comparable.
        text_obj = result.get("text", "")
        text = text_obj if isinstance(text_obj, str) else str(result)
        returned_bytes += len(text.encode("utf-8"))
        transcript_tokens += _fixed_token_count(text)
        transcript_tokens += _tokenize_call(call)
        if result.get("is_stale") is True or result.get("index_used") is False:
            stale_fallback += 1
        # AC-12: collect returned evidence ids so the harness can
        # compute real recall/precision from the union of (a) the
        # per-call expected ids and (b) any explicit evidence_ids
        # in the executor's payload. This MUST happen on the same
        # pass that records tool calls; a separate replay would
        # double the executor invocations and understate the
        # call counter relative to real executor work.
        returned_ids: object = result.get("evidence_ids", ())
        if isinstance(returned_ids, (list, tuple)):
            for ev_id in returned_ids:
                if isinstance(ev_id, str) and ev_id:
                    returned_evidence_ids.add(ev_id)
    # AC-12 (full transcript): derive ``final_evidence_tokens`` from
    # the actual returned evidence ids rather than a constant so
    # the transcript reflects the compact evidence context the
    # agent keeps after the flow ends. A ``final_evidence_tokens``
    # argument may still be supplied as a lower bound or override;
    # we use ``max(supplied, derived_from_returned)`` so callers
    # can pin a known-context budget without losing the derived
    # signal when more ids are actually returned.
    derived_final_evidence_tokens = sum(
        len(ev_id.split()) for ev_id in returned_evidence_ids
    )
    effective_final_evidence_tokens = max(
        final_evidence_tokens,
        derived_final_evidence_tokens,
    )
    transcript_tokens += effective_final_evidence_tokens
    wall_time = clock.now() - start
    # AC-12: evidence recall/precision are derived from the actual
    # returned evidence ids, not hardcoded. The benchmark harness
    # uses the union of per-call expected ids as the truth set
    # and the actual returned ids as the prediction set.
    expected_ids: set[str] = set()
    for call in script:
        for ev_id in call.expected_evidence_ids:
            if isinstance(ev_id, str) and ev_id:
                expected_ids.add(ev_id)
    recall, precision = _evidence_metrics(expected_ids, returned_evidence_ids)
    counters = BenchmarkCounters(
        tool_calls=len(script),
        returned_bytes=returned_bytes,
        transcript_tokens=transcript_tokens,
        wall_time_seconds=wall_time,
        stale_fallback_events=stale_fallback,
        evidence_recall=recall,
        evidence_precision=precision,
    )
    return counters, returned_evidence_ids


def _evidence_metrics(
    expected: set[str],
    returned: set[str],
) -> tuple[float, float]:
    """Return (recall, precision) as floats in [0.0, 1.0].

    Both return 1.0 when the expected set is empty (the fixture
    did not require any specific evidence). When returned is
    empty but expected is not, both are 0.0.
    """
    if not expected:
        return 1.0, 1.0
    if not returned:
        return 0.0, 0.0
    matched = expected & returned
    recall = len(matched) / len(expected)
    precision = len(matched) / len(returned)
    return recall, precision


# --- Fixture question builders (Q1-Q13) ---
# Ponytail: fixtures live in :mod:`ralph.mcp.explore._bench_fixtures`
# so this harness stays under the per-file line ceiling. Re-export the
# question-builder functions and the workflow registry so
# ``from ralph.mcp.explore.bench import REQUIRED_FIXTURES`` etc.
# remains source-compat.


def run_benchmark(
    fixture: BenchmarkFixture,
    *,
    baseline_executor: Callable[[ScriptedCall], Mapping[str, object]],
    indexed_executor: Callable[[ScriptedCall], Mapping[str, object]],
    clock: Clock | None = None,
    expected_evidence_ids: Sequence[str] | None = None,
    catalog_tokens: int | None = None,
    final_evidence_tokens: int | None = None,
    visible_tool_catalog: Sequence[tuple[str, str]] | None = None,
) -> BenchmarkResult:
    """Run a fixture's baseline and indexed flows and produce a result.

    ``baseline_executor`` and ``indexed_executor`` are scripted flows
    over the existing MCP handlers; they MUST NOT call a live LLM
    agent. They are pure functions of (call) -> result-dict.

    AC-07: each executor is invoked exactly ``len(script)`` times
    (one invocation per scripted call) so the tool-call counters
    equal the number of executor invocations a counting harness
    observes. Recall/precision are derived from the fixture's
    ``expected_evidence_ids`` truth set and the indexed executor's
    actual returned ``evidence_ids`` collected during the SAME
    pass that records the indexed counters. The previous
    implementation replayed the indexed script solely to gather
    evidence ids, which silently doubled executor invocations
    while leaving the reported counter at ``len(script)``; that
    double-execution broke the "no extra scripted tool calls"
    budget and let counting executors observe hidden work.

    Callers may override the truth set via ``expected_evidence_ids``;
    the default is the fixture's declared ``expected_evidence_ids``
    (a non-empty, unique tuple, by contract).

    AC-12 (full transcript): ``catalog_tokens`` (bounded serialized
    visible tool descriptions/input schemas) and
    ``final_evidence_tokens`` (the compact evidence context the
    agent keeps after the flow ends) are added once per script so
    the transcript cost matches the research gate's "full scripted
    transcript" definition. Defaults of ``0`` keep the harness
    back-compat with callers that pass only the script.
    """
    # AC-12: derive catalog + final-evidence tokens from the
    # fixture itself when the caller did not supply them. The
    # fixture owns these constants; the harness does NOT rely on
    # optional zero defaults for required transcript pieces.
    derived_catalog_tokens = (
        catalog_tokens
        if catalog_tokens is not None
        else (
            sum(tool_catalog_tokens(visible_tool_catalog).values())
            if visible_tool_catalog
            else fixture.catalog_tokens
        )
    )
    derived_final_evidence_tokens = (
        final_evidence_tokens
        if final_evidence_tokens is not None
        else fixture.final_evidence_tokens
    )
    clk = clock or SystemClock()
    baseline_counters, _baseline_returned_ids = _run_script(
        fixture.baseline_script,
        executor=baseline_executor,
        clock=clk,
        catalog_tokens=derived_catalog_tokens,
        final_evidence_tokens=derived_final_evidence_tokens,
    )
    indexed_counters, indexed_returned_ids = _run_script(
        fixture.indexed_script,
        executor=indexed_executor,
        clock=clk,
        catalog_tokens=derived_catalog_tokens,
        final_evidence_tokens=derived_final_evidence_tokens,
    )
    truth = (
        tuple(expected_evidence_ids)
        if expected_evidence_ids is not None
        else fixture.expected_evidence_ids
    )
    recall, precision = _evidence_metrics(set(truth), indexed_returned_ids)
    indexed_counters = BenchmarkCounters(
        tool_calls=indexed_counters.tool_calls,
        returned_bytes=indexed_counters.returned_bytes,
        transcript_tokens=indexed_counters.transcript_tokens,
        wall_time_seconds=indexed_counters.wall_time_seconds,
        stale_fallback_events=indexed_counters.stale_fallback_events,
        evidence_recall=recall,
        evidence_precision=precision,
    )
    return BenchmarkResult(
        question_id=fixture.question_id,
        baseline=baseline_counters,
        indexed=indexed_counters,
        notes=(fixture.description,),
    )



__all__ = [
    "ALL_FIXTURES",
    "EXTENDED_FIXTURES",
    "REQUIRED_BENCH_WORKFLOW_IDS",
    "REQUIRED_FIXTURES",
    "BenchmarkFixture",
    "BenchmarkResult",
    "ScriptedCall",
    "run_benchmark",
]
