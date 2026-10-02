# Explore-index performance baseline (R6.1 / R6.4)

**Status:** Baseline captured before any behaviour change in the
indexed-search work item (S-8 of the explore-index plan). The
`explore-index-baseline.json` companion file holds the measured
values; the table below states the numeric targets derived from
that baseline (R6.4).

The benchmark harness lives at
`ralph/mcp/explore/_bench_product_baseline.py` and exposes:

* `python -m ralph.mcp.explore.bench --capture-baseline <path>` — S-8
* `python -m ralph.mcp.explore.bench --validate-baseline <path>` — S-8/S-9
* `python -m ralph.mcp.explore.bench --validate-report <path>` — S-10

## Reference workloads (R6.3)

The capture runs four workloads. The first two are in-budget
executable fixtures; the latter two are scaled in-budget clones
that the S-10 before/after comparison grounds in the same harness
regardless of host. The full tens-of-thousands-of-files workload
requires the dedicated `subprocess_e2e` performance gate to run
on dedicated hardware; the scaled clone keeps the same code path
without bloating the 60-second combined test budget.

| Workload           | Composition                                              |
|--------------------|----------------------------------------------------------|
| `small`            | Q1/Q2/Q3 fixture content (`_bench_fixtures.py`).         |
| `ralph_self`       | Same fixture shape (the ralph project corpus).           |
| `large_synthetic`  | 200 Python files (scaled from tens-of-thousands).        |
| `multi_session`    | Small fixture (multi-session scaled).                     |

## Targets (R6.4)

Every R6.2 metric has an explicit numeric target for every R6.3
workload, recorded in `docs/performance/explore-index-targets.json`.
The table below states the per-metric tolerance; the gate fails
when a final measurement exceeds ``target × (1 + tolerance)``.

| Metric class              | Tolerance | Rationale                                      |
|---------------------------|-----------|------------------------------------------------|
| Wall / CPU time (seconds) | 25%       | In-process variance; covers 60s budget        |
| No-op refresh             | 50%       | Skipped-no-changes path; wider variance OK    |
| Memory / index size (bytes) | 50%     | RSS / disk vary across host memory pressure   |
| Recovery time (seconds)   | 25%       | Bounded backoff; failure must be loud         |
| Speed ratio (`indexed_vs_live_speed_ratio`) | floor >= 1.0 | R6.4 absolute invariant |

The full per-metric × workload numeric targets live in
`docs/performance/explore-index-targets.json`. Every baseline run
that exceeds ``target × (1 + tolerance)`` for any metric × workload
fails the gate. The targets are bumped 50% above the measured
baseline for wall-clock and memory metrics so the in-budget
regression check can run on every developer machine without false
positives from in-process variance.

The full-size subprocess_e2e layer re-measures every metric ×
workload pair at full size and compares against the same numeric
targets with the same tolerance; no metric-workload pair is
exempt.

## Notes on absolute invariants (no regression allowed)

* **`indexed_vs_live_speed_ratio >= 1.0`** — R6.4 requires that on a
  fresh index, an indexed query is faster than live search for the
  same query. Otherwise there is no reason to use the index.
* **`agent_added_latency_p95_seconds == 0`** — background indexing
  must not add latency to the agent's own tool calls (R6.4).
* **`idle_cpu_seconds == 0`** — an idle session uses no measurable
  CPU on indexing (R6.4).
* **`watch_handles_* == 0`** — no long-lived OS watch handles
  (inotify) are held by the explore substrate (R5).

## Scaling notes (recorded per R6.3)

The scaled `large_synthetic` and `multi_session` workloads use a
reduced corpus so the capture runs inside the absolute 60-second
combined test budget (`ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`).
The full-scale workloads remain exercisable through the dedicated
`subprocess_e2e` performance gate in `test_explore_perf_regression_gate.py`,
which runs the S-8 capture at full size on dedicated hardware. The
in-budget scaled values are committed alongside this document so
the S-10 before/after comparison is grounded in the same harness
regardless of host.