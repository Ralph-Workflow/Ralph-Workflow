from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from ralph.mcp.explore._bench_r6_query_sampler import sample_query_latencies

_COLD_SPIKE_SECONDS: Final[float] = 0.01
_STEADY_SECONDS: Final[float] = 0.000_05
_RAW_OVERHEAD_P50: Final[float] = 0.0
_SAMPLES: Final[int] = 5
_QUERY_KEYS: Final[tuple[str, ...]] = (
    "indexed_query_p50_seconds",
    "indexed_query_p95_seconds",
    "indexed_query_p99_seconds",
    "live_query_p50_seconds",
    "live_query_p95_seconds",
    "live_query_p99_seconds",
    "indexed_vs_live_speed_ratio",
    "agent_added_latency_p95_seconds",
)


class _FakeClock:
    def __init__(self) -> None:
        self._t: float = 0.0

    def __call__(self) -> float:
        return self._t

    def advance(self, by: float) -> None:
        self._t += by


class _FakeGrepHandler:
    def __init__(self, *, cold: float, steady: float, clock: _FakeClock) -> None:
        self._cold = cold
        self._steady = steady
        self._clock = clock
        self._first_call_per_mode: dict[str, bool] = {"auto": True, "never": True}

    def __call__(
        self,
        session: object,
        workspace: object,
        params: dict[str, object],
    ) -> object:
        use_index_raw = params["use_index"]
        assert isinstance(use_index_raw, str)
        use_index = use_index_raw
        is_first = self._first_call_per_mode[use_index]
        self._first_call_per_mode[use_index] = False
        self._clock.advance(self._cold if is_first else self._steady)
        return None


def _assert_only_steady_metrics(metrics: Mapping[str, float]) -> None:
    assert set(metrics) == set(_QUERY_KEYS), f"unexpected metric keys: {sorted(metrics)}"
    spike_carrying_keys: tuple[str, ...] = (
        "indexed_query_p95_seconds",
        "indexed_query_p99_seconds",
        "live_query_p95_seconds",
        "live_query_p99_seconds",
        "agent_added_latency_p95_seconds",
    )
    for key in spike_carrying_keys:
        measured = metrics[key]
        assert math.isclose(measured, _STEADY_SECONDS, rel_tol=1e-6, abs_tol=1e-9), (
            f"{key}={measured} != steady {_STEADY_SECONDS} (cold spike leaked into percentile tail)"
        )
    assert math.isclose(metrics["indexed_vs_live_speed_ratio"], 1.0, rel_tol=1e-6, abs_tol=1e-9)


def test_sample_query_latencies_excludes_first_call_cold_spike() -> None:
    clock = _FakeClock()
    handler = _FakeGrepHandler(cold=_COLD_SPIKE_SECONDS, steady=_STEADY_SECONDS, clock=clock)

    metrics = sample_query_latencies(
        grep_handler=handler,
        session=None,
        workspace=None,
        perf_counter=clock,
        raw_overhead_p50=_RAW_OVERHEAD_P50,
        samples=_SAMPLES,
    )

    _assert_only_steady_metrics(metrics)
