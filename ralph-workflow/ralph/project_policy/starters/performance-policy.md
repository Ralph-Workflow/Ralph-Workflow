<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: performance-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, deletes this banner, and adds the completion
marker. Readiness stays blocked while this banner or any placeholder token
remains. -->

# Performance Policy

This policy applies while the project declares performance budgets
or benchmarking tooling. If the project permanently drops them,
remove this policy file in the same workflow or record the change
under Exceptions.

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

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If the project is too young for a fact to be settled, record the best
current answer plus the condition that will settle it, e.g.
"none yet (assumed <date>; revisit when <trigger>)" — a future agent must
be able to tell a settled fact from a provisional one at a glance. Then
delete this comment. -->

RALPH-FACT: performance_objectives: PROJECT-FACT-UNRESOLVED
RALPH-FACT: performance_budget: PROJECT-FACT-UNRESOLVED
RALPH-FACT: representative_workload: PROJECT-FACT-UNRESOLVED
RALPH-FACT: environment_specification: PROJECT-FACT-UNRESOLVED
RALPH-FACT: benchmark_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: profiling_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: regression_threshold: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_benchmark_integration: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* JUSTIFY optimization work with measurement.
* PRESERVE correctness, readability, and maintainability during
  optimization.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes a benchmark, a budget, a profiling tool, or the
  representative workload.

An agent MUST NOT:

* Invent numerical targets where none exist in repository evidence.
* Optimize at the cost of correctness, readability, or maintainability.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; only a gate that truly cannot exist may be recorded as
inapplicable with a reason and the condition that would create it. Then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean benchmark / profiling run
within the documented budget. On regression, report the affected code
path, the regression magnitude, and the cause.

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

## Living document contract

This policy is a living document. It MUST evolve as the project grows:
update the resolved facts, commands, and requirements whenever verified
project reality changes (new frameworks, new commands, new structure).
Two guardrails bound every amendment:

* Conflicts between this policy's generic defaults and the project's
  established practice are resolved in
  favor of the existing project policy — adapt this file to verified
  project reality, never the reverse. A looser project practice is
  NOT such a conflict: keep the stronger requirement unless a
  documented exception narrows it.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: performance-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` comment; its presence
  certifies this file passed validation when it was last amended.