"""Pure, injectable query-latency sampler for the R6 perf regression gate.

Split from ``_bench_r6_metrics`` so each module stays under the
repository file-size cap. The sampler is the testable seam behind
``_measure_query_latency``: the production caller hands in the real
handler and the real ``time.perf_counter``, the test hands in a
plain-class fake with a scripted ``perf_counter`` sequence and
asserts on the returned metric values. The seam exists to prove
the warmup that excludes the first per-mode call from the sampled
loop (R6.6 "no flaky results").
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ralph.mcp.explore._bench_product_baseline import nearest_rank_p95


class _QueryCallable(Protocol):
    """Callable shape for the ``grep_handler`` injection in ``sample_query_latencies``.

    The sampler calls the handler with ``(session, workspace, params)``
    where ``params`` is the per-call argument dict. The Protocol
    bound takes ``object`` for the session / workspace parameters (the
    sampler does not constrain them -- callers pass whatever the
    handler accepts) so test fakes can satisfy it without inheriting
    from the production ``CoordinationSessionLike`` / ``Workspace``
    Protocols. Production injects
    ``cast(_QueryCallable, handle_grep_files)`` because the
    concrete production handler's parameter types are stricter than
    this Protocol's ``object`` parameters, and contravariance
    correctly accepts a narrower-input function in place of a
    wider-input one.
    """

    def __call__(
        self,
        session: object,
        workspace: object,
        params: dict[str, object],
    ) -> object: ...


def sample_query_latencies(
    *,
    grep_handler: _QueryCallable,
    session: object,
    workspace: object,
    perf_counter: Callable[[], float],
    raw_overhead_p50: float,
    samples: int = 5,
) -> dict[str, float]:
    """Time indexed and live grep calls over ``samples`` iterations each.

    Each iteration calls ``grep_handler(session, workspace, params)``
    once with ``use_index="auto"`` and once with ``use_index="never"``;
    the duration of every call is measured by ``perf_counter()`` and
    recorded in the per-mode samples list. Percentiles are computed
    with the same arithmetic the inlined harness used before this
    extraction (``sorted[len // 2]`` for p50, ``nearest_rank_p95``,
    and ``sorted[-1]`` for p99). The returned dict matches the
    metric shape ``_measure_query_latency`` historically returned
    so the extraction is behaviour-preserving.

    One untimed warmup call is issued per mode before the sampled
    loop mirrors the ``do_warmup=True`` pattern the cold-build /
    refresh measurements already use: the first call per mode pays
    a one-time SQLite query-plan compile + page-cache fill cost
    that otherwise dominates the five-sample p95/p99 (the
    ``nearest_rank_p95`` of 5 samples is the sorted maximum).
    """
    # Untimed warmup: amortise the per-mode cold-start cost (SQLite
    # query-plan compile + page-cache fill) so the sampled loop sees
    # only steady-state behaviour. Mirrors ``do_warmup=True`` in the
    # cold-build / refresh harnesses. Intentionally not measured.
    grep_handler(
        session,
        workspace,
        {
            "pattern": "hello",
            "path": ".",
            "regex": False,
            "case_sensitive": False,
            "use_index": "auto",
        },
    )
    grep_handler(
        session,
        workspace,
        {
            "pattern": "hello",
            "path": ".",
            "regex": False,
            "case_sensitive": False,
            "use_index": "never",
        },
    )
    indexed_samples: list[float] = []
    live_samples: list[float] = []
    for _ in range(samples):
        start = perf_counter()
        grep_handler(
            session,
            workspace,
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        )
        indexed_samples.append(perf_counter() - start)
        start = perf_counter()
        grep_handler(
            session,
            workspace,
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "never",
            },
        )
        live_samples.append(perf_counter() - start)
    indexed_p50 = sorted(indexed_samples)[len(indexed_samples) // 2]
    indexed_p95 = nearest_rank_p95(indexed_samples)
    indexed_p99 = sorted(indexed_samples)[-1]
    live_p50 = sorted(live_samples)[len(live_samples) // 2]
    live_p95 = nearest_rank_p95(live_samples)
    live_p99 = sorted(live_samples)[-1]
    speed_ratio = (live_p50 / indexed_p50) if indexed_p50 > 0 else 0.0
    agent_added = max(0.0, nearest_rank_p95(indexed_samples) - raw_overhead_p50)
    return {
        "indexed_query_p50_seconds": indexed_p50,
        "indexed_query_p95_seconds": indexed_p95,
        "indexed_query_p99_seconds": indexed_p99,
        "live_query_p50_seconds": live_p50,
        "live_query_p95_seconds": live_p95,
        "live_query_p99_seconds": live_p99,
        "indexed_vs_live_speed_ratio": speed_ratio,
        "agent_added_latency_p95_seconds": agent_added,
    }
