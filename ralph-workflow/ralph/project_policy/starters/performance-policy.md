<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: performance-policy.md -->

# Performance Policy

> This file is REQUIRED only when the validator detects explicit
> performance signals (any of the paths in `markers.PERF_SIGNAL_PATHS`
> exists OR any manifest contains a perf-dep substring). When the
> domain is not present, REMOVE this file or document its inapplicability
> explicitly under "Exceptions".

## Purpose and scope

This policy governs performance: user-visible or operational
performance objectives and their measurement units, representative
workloads and environments, baselines and budgets, benchmark and
profiling commands, regression detection, optimization justification,
and preservation of correctness, readability, and maintainability.

## Default requirements

* User-visible or operational performance objectives MUST be documented
  with measurement units (milliseconds, requests/second, frame time,
  startup time, render time, etc.).
* Representative workloads, environments, datasets, and warm/cold
  conditions MUST be documented so benchmarks are reproducible.
* Baselines and any enforceable budgets or thresholds MUST be documented
  with measurement methodology.
* Optimization work MUST be justified by measurement, not by intuition.
  "We could make this faster" without a benchmark is not a justification.
* Optimization MUST preserve correctness, readability, and
  maintainability. The "fast and broken" outcome is forbidden.

## Project facts to resolve

* RALPH-FACT: performance_objectives: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: performance_budget: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: representative_workload: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: environment_specification: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: benchmark_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: profiling_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: regression_threshold: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: ci_benchmark_integration: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT existing benchmarks, budgets, and profiling tools before any
  performance change.
* PRESERVE stricter existing performance rules; adapt rather than weaken.
* REPLACE every starter placeholder with a verified value.
* JUSTIFY optimization work with measurement.
* PRESERVE correctness, readability, and maintainability during
  optimization.

The agent MUST NOT:

* Invent numerical targets where none exist in repository evidence.
* Optimize at the cost of correctness, readability, or maintainability.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean benchmark / profiling run
within the documented budget. On regression, the agent MUST report the
affected code path, the regression magnitude, and the cause.

## Exceptions

A documented exception to a performance budget requires a documented
rationale, scope, owner, and review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A performance budget is added or changed.
* A new benchmark or profiling tool is added.
* The representative workload changes.

## Research basis

* publisher: Google SRE Book
  title: "Embracing Risk"
  http: https://sre.google/sre-book/embracing-risk/
  review date: 2026-07-11

* publisher: Brendan Gregg
  title: "Systems Performance (2nd Edition)"
  http: https://www.brendangregg.com/systems-performance-2nd-edition-book.html
  review date: 2026-07-11

* publisher: Google Web Fundamentals
  title: "Performance Budgets"
  http: https://web.dev/articles/performance-budgets-101
  review date: 2026-07-11

* publisher: High Performance Browser Networking (Ilya Grigorik)
  title: "Latency Budget"
  http: https://hpbn.co/primer-on-web-performance/
  review date: 2026-07-11

## Ralph markers

* Policy id: `<!-- ralph-policy-id: performance-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: `the the project-policy-complete comment identifier comment` (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).