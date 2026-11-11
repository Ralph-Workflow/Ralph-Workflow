
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Final, Protocol, cast

from ralph.mcp.explore._bench_fixtures import (
    ALL_FIXTURES,
    EXTENDED_FIXTURES,
    REQUIRED_BENCH_WORKFLOW_IDS,
    REQUIRED_FIXTURES,
)
from ralph.mcp.explore._bench_product_baseline import (
    flow_limits_seconds,
    gate_product_baseline,
    load_product_baseline_limits,
    main,
    measure_representative_flows,
    nearest_rank_p95,
    run_product_baseline,
)
from ralph.mcp.explore._bench_r6_metrics import (
    capture_baseline,
    run_capture_baseline,
    run_validate_baseline,
    run_validate_report,
    validate_baseline,
    validate_report,
)
from ralph.mcp.explore._bench_types import (
    BenchmarkCounters,
    BenchmarkFixture,
    BenchmarkResult,
    ScriptedCall,
)

if __name__ == "__main__":
    raise SystemExit(main())


def _fixed_token_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


def _serialize_tool_spec(spec: object) -> str:
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

    def now(self) -> float: ...


class SystemClock:

    def now(self) -> float:
        return time.monotonic()


def _tokenize_call(call: ScriptedCall) -> int:
    payload = f"{call.tool} {sorted(call.params.items())}"
    return _fixed_token_count(payload)


_LEGACY_TUPLE_ARITY: Final[int] = 2


def tool_catalog_tokens(
    tool_specs: Iterable[object],
) -> dict[str, int]:
    tokens: dict[str, int] = {}
    for spec in tool_specs:
        if isinstance(spec, tuple) and len(spec) >= _LEGACY_TUPLE_ARITY:
            name = str(spec[0])
            description_obj = spec[1]
            if isinstance(description_obj, str):
                description = description_obj
            else:
                description = _serialize_tool_spec(description_obj)
        else:
            serialized = _serialize_tool_spec(spec)
            name_obj: object = getattr(spec, "name", None)
            name = str(name_obj) if isinstance(name_obj, str) else serialized
            description = serialized
        tokens[name] = _fixed_token_count(description)
    return tokens


def derive_visible_catalog(
    mcp_config: object | None = None,
) -> tuple[object, ...]:
    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.tools.bridge._registry import tool_specs as _bridge_tool_specs

    if mcp_config is None:
        cfg: McpConfig = McpConfig()
    else:
        cfg = cast(
            "McpConfig", mcp_config
        )
    try:
        specs = _bridge_tool_specs(cfg)
    except Exception:
        from ralph.mcp.tools.names import RalphToolName

        return tuple(
            (member.value, {"type": "object", "properties": {}}) for member in RalphToolName
        )

    catalog: list[object] = []
    for spec in specs:
        metadata = spec.metadata
        name = str(metadata.definition.name)
        catalog.append((name, metadata.definition))
    return tuple(catalog)


def _registered_tool_definitions() -> tuple[object, ...]:

    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.tools.bridge._registry import tool_specs as _bridge_tool_specs

    try:
        return tuple(spec.metadata.definition for spec in _bridge_tool_specs(McpConfig()))
    except Exception:
        return derive_visible_catalog()


def _run_script(
    script: Sequence[ScriptedCall],
    *,
    executor: Callable[[ScriptedCall], Mapping[str, object]],
    clock: Clock,
    catalog_tokens: int = 0,
    final_evidence_tokens: int = 0,
) -> tuple[BenchmarkCounters, set[str]]:
    start = clock.now()
    returned_bytes = 0
    transcript_tokens = catalog_tokens
    stale_fallback = 0
    returned_evidence_ids: set[str] = set()
    for call in script:
        result = executor(call)
        text_obj = result.get("text", "")
        text = text_obj if isinstance(text_obj, str) else str(result)
        returned_bytes += len(text.encode("utf-8"))
        transcript_tokens += _fixed_token_count(text)
        transcript_tokens += _tokenize_call(call)
        if result.get("is_stale") is True or result.get("index_used") is False:
            stale_fallback += 1
        returned_ids: object = result.get("evidence_ids", ())
        if isinstance(returned_ids, (list, tuple)):
            for ev_id in returned_ids:
                if isinstance(ev_id, str) and ev_id:
                    returned_evidence_ids.add(ev_id)
    derived_final_evidence_tokens = sum(len(ev_id.split()) for ev_id in returned_evidence_ids)
    effective_final_evidence_tokens = max(
        final_evidence_tokens,
        derived_final_evidence_tokens,
    )
    transcript_tokens += effective_final_evidence_tokens
    wall_time = clock.now() - start
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
    if not expected:
        return 1.0, 1.0
    if not returned:
        return 0.0, 0.0
    matched = expected & returned
    recall = len(matched) / len(expected)
    precision = len(matched) / len(returned)
    return recall, precision




def run_benchmark(
    fixture: BenchmarkFixture,
    *,
    baseline_executor: Callable[[ScriptedCall], Mapping[str, object]],
    indexed_executor: Callable[[ScriptedCall], Mapping[str, object]],
    clock: Clock | None = None,
    expected_evidence_ids: Sequence[str] | None = None,
    catalog_tokens: int | None = None,
    final_evidence_tokens: int | None = None,
    visible_tool_catalog: Sequence[object] | None = None,
) -> BenchmarkResult:
    derived_catalog_tokens = (
        catalog_tokens
        if catalog_tokens is not None
        else (
            sum(tool_catalog_tokens(visible_tool_catalog).values())
            if visible_tool_catalog is not None
            else sum(tool_catalog_tokens(_registered_tool_definitions()).values())
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
    "SystemClock",
    "capture_baseline",
    "derive_visible_catalog",
    "flow_limits_seconds",
    "gate_product_baseline",
    "load_product_baseline_limits",
    "main",
    "measure_representative_flows",
    "nearest_rank_p95",
    "run_benchmark",
    "run_capture_baseline",
    "run_product_baseline",
    "run_validate_baseline",
    "run_validate_report",
    "tool_catalog_tokens",
    "validate_baseline",
    "validate_report",
]
