
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ralph.mcp.explore._bench_product_baseline import nearest_rank_p95


class _QueryCallable(Protocol):

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
