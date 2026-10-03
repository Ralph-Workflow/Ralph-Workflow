"""Cold-startup removal of sample_query_latencies must be a behaviour contract.

The R6 query-latency sampler (``sample_query_latencies`` in
``ralph.mcp.explore._bench_r6_metrics``) times ``handle_grep_files`` for
both indexed (``use_index="auto"``) and live (``use_index="never"``)
searches. The very first call per mode pays a one-time cold-start cost
(SQLite/FTS5 query-plan compile, page-cache fill, freshness-check on
the freshly opened store). With the pinned five-sample loop and
``nearest_rank_p95`` (``sorted[ceil(0.95*5)-1]`` = ``sorted[-1]`` = max)
that cold sample dominates the indexed p95/p99 percentile, so a single
spike on a loaded developer machine can flip the small-workload
``indexed_query_p95_seconds`` past the documented target ceiling even
when every steady-state call is well below it.

The expected remedy is an untimed warmup call per mode before the
sampled loop, mirroring the existing ``do_warmup=True`` pattern the
cold-build/refresh measurements already use. This test pins the
contract on the sampler in isolation so a behaviour-preserving refactor
keeps the test green while removing the warmup makes it red.

Portfolio admission
------------------

- Decision: REPLACE (this deterministic unit control replaces
  on-demand manual flake reproduction, which is unowned one-off
  evidence).
- Owner: ralph-workflow maintainers.
- Trigger: any change to ``_bench_r6_metrics._measure_query_latency``
  /``sample_query_latencies`` query sampling.
- Marginal cost: < 0.05 s; no real I/O, no subprocess, no
  ``time.sleep``.
- Lifecycle-review condition: remove this test if query sampling
  moves behind a subprocess boundary that asserts its own warmup.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from ralph.mcp.explore._bench_r6_query_sampler import sample_query_latencies

#: Cold spike and steady-state latencies injected through the
#: fake ``perf_counter``. The cold spike is sized to exceed the
#: small-workload ``indexed_query_p95_seconds`` target ceiling by
#: ~30% (target is documented in
#: ``docs/performance/explore-index-targets.json``; we do not read
#: the target file here to keep the test fully hermetic). The
#: steady-state latency is orders of magnitude smaller so the
#: post-warmup percentiles cannot collide with the spike.
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
    """Deterministic ``perf_counter`` replacement.

    Each ``advance(by)`` call simulates the elapsed time of one
    ``grep_handler`` invocation; ``__call__`` returns the current
    monotonic value. Tests compose ``.advance`` with a cold-vs-steady
    scripted sequence and the sampler reads ``__call__`` before and
    after each handler call to derive the recorded duration.
    """

    def __init__(self) -> None:
        self._t: float = 0.0

    def __call__(self) -> float:
        return self._t

    def advance(self, by: float) -> None:
        self._t += by


class _FakeGrepHandler:
    """``grep_handler`` fake whose first per-mode call is a cold spike.

    The first invocation with ``use_index="auto"`` advances the fake
    clock by ``cold`` seconds; the first with ``use_index="never"``
    does the same. Every later invocation (per mode) advances by
    ``steady`` seconds. With the S-4 sampler the cold call IS one of
    the five timed samples, so the indexed/live p95/p99 percentiles
    pick up the spike; with the planned S-2 warmup the cold call is
    consumed before the timed loop starts and only steady values reach
    the percentile math.

    The parameter types match the production
    ``handle_grep_files(session, workspace, params)`` shape so the
    fake is structurally assignable to the sampler's
    ``grep_handler`` callable type, and the return type is
    ``object`` because the sampler discards the value (the real
    handler returns a ``ToolResult``; the fake returns ``None``,
    which is a subtype of ``object``).
    """

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
    """Assert percentile metrics reflect steady-state latency, not the spike.

    Only the percentiles the plan calls out are inspected:

    - ``indexed_query_p95_seconds`` / ``p99``
    - ``live_query_p95_seconds`` / ``p99``
    - ``agent_added_latency_p95_seconds``

    These four (plus the speed-ratio) are the metrics the cold spike
    can leak into under the S-4 sampler (``nearest_rank_p95`` of 5
    samples is the sorted maximum, so a single cold call lands in
    p95/p99). The assertion fails the red run before S-2 adds the
    warmup and only passes once the cold call is excluded from the
    sampled loop. Approximate equality (``math.isclose``) absorbs
    the inevitable float-arithmetic noise in 5e-05 second samples
    while still rejecting the 200x-larger cold spike.
    """
    assert set(metrics) == set(_QUERY_KEYS), f"unexpected metric keys: {sorted(metrics)}"
    # p50 of 5 samples is sorted[2]. Both with and without the
    # cold spike the median falls on the steady value -- so the
    # p50 carries no cold signal and we skip an assertion on it.
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
            f"{key}={measured} != steady {_STEADY_SECONDS} "
            f"(cold spike leaked into percentile tail)"
        )
    # Speed ratio = live_p50 / indexed_p50; both equal steady so
    # the ratio is 1.0 either way. Kept here so any future change
    # that breaks the ratio shape surfaces in this test.
    assert math.isclose(metrics["indexed_vs_live_speed_ratio"], 1.0, rel_tol=1e-6, abs_tol=1e-9)


def test_sample_query_latencies_excludes_first_call_cold_spike() -> None:
    """Returned metrics must reflect steady-state latency, not the first call.

    Constructs a sampler with fake ``grep_handler`` and
    ``perf_counter``. The fake handler advances the fake clock by
    ``_COLD_SPIKE_SECONDS`` on its first per-mode call and
    ``_STEADY_SECONDS`` thereafter; the fake clock exposes the
    sampler-readable time. After the call every percentile must
    equal ``_STEADY_SECONDS`` -- this is red on the pre-S-2 tree
    (p95/p99 pick up the cold spike, ``ceil(0.95*5)-1 == 4`` so
    p95 is the sorted maximum) and green only after the warmup
    lands.
    """
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
