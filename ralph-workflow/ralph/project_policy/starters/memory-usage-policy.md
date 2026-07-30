<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: memory-usage-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Memory-Usage Policy

This policy applies while the project declares memory budgets or
soak/leak tooling. If the project permanently drops them, remove
this policy file in the same workflow or record the change under
Exceptions.

## Purpose and scope

This policy governs memory usage: peak memory, steady-state memory,
and unbounded growth; ownership and lifecycle rules for caches,
buffers, queues, collections, subscriptions, handles, and other
retained resources; bounds, eviction, backpressure, cleanup, and
shutdown expectations; leak detection and soak-test methods; and
treatment of large inputs, streaming, batching, and
allocation-sensitive paths.

## Default requirements

* Changes affecting allocation, retention, lifecycle, or volume-driven state
  MUST respect documented memory limits, growth expectations, and workloads.
* Ownership and lifecycle rules for caches, buffers, queues,
  collections, subscriptions, and handles MUST be documented.
* Bounds, eviction, backpressure, cleanup, and shutdown expectations
  MUST be documented.
* Leak detection, profiling, stress-test, or soak-test methods MUST be
  documented and runnable.
* Large inputs, streaming, batching, and allocation-sensitive paths
  MUST be identified and treated explicitly.
* Acceptable baseline and regression thresholds MUST be documented when
  evidence supports them.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If a fact cannot be resolved yet (project too young, tool not chosen, value
not knowable), defer it with the RALPH-PENDING form "RALPH-PENDING (assumed
<date>); review trigger: <trigger>" — it reaches readiness and a dev-cycle
agent resolves it when its trigger fires. Then
delete this comment. -->

RALPH-FACT: peak_memory_baseline: PROJECT-FACT-UNRESOLVED
RALPH-FACT: steady_state_memory_baseline: PROJECT-FACT-UNRESOLVED
RALPH-FACT: cache_lifecycle_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: bounded_accumulator_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: leak_detection_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: soak_test_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: large_input_handling_pattern: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_soak_integration: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* DISTINGUISH peak memory, steady-state memory, and unbounded growth for
  changes affecting allocation, retention, lifecycle, or volume-driven state.
* PREFER existing lifecycle patterns over new collections.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes a cache, an accumulator cap, a soak test, or a
  leak detector.

An agent MUST NOT:

* Add a long-lived or externally/volume-driven unbounded collection without
  a documented cap, eviction policy, backpressure rule, or cleanup path.
* Fabricate a fixed memory budget when the project has not established
  one.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; a gate that applies but is not wired yet (for example the tool is
not installed on a new project) is recorded as a RALPH-PENDING deferral —
`RALPH-PENDING: <approved-tool> (assumed <date>); review trigger: <trigger>`
— which reaches readiness and is resolved by a later dev cycle when its
trigger fires; only a gate that truly cannot EVER exist is recorded as
inapplicable with a reason and the condition that would create it. Then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the leak / soak audit.
On regression, report the affected code path, the regression magnitude,
and the cause.

## Exceptions

A documented exception to a memory budget requires a documented
rationale, scope, owner, and review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new cache, queue, or buffer is introduced.
* A bounded accumulator's cap or eviction policy changes.
* A new soak test or leak detector is added.

## Research basis

* publisher: The Go Authors
  title: "A Guide to the Go Garbage Collector"
  http: https://go.dev/doc/gc-guide
  review date: 2026-07-11

* publisher: Brendan Gregg
  title: "Systems Performance (2nd Edition)"
  http: https://www.brendangregg.com/systems-performance-2nd-edition-book.html
  review date: 2026-07-11

* publisher: Martin Thompson
  title: "Mechanical Sympathy: Memory Hierarchy"
  http: https://mechanical-sympathy.blogspot.com/
  review date: 2026-07-11

* publisher: Python Software Foundation
  title: "Memory Management (Python/C API)"
  http: https://docs.python.org/3/c-api/memory.html
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

* Policy id: `<!-- ralph-policy-id: memory-usage-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
