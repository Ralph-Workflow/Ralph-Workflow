<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: memory-usage-policy.md -->

# Memory-Usage Policy

> This file is REQUIRED only when the validator detects explicit
> memory signals (any of the paths in `markers.MEMORY_SIGNAL_PATHS`
> exists OR any manifest contains a memory-dep substring). When the
> domain is not present, REMOVE this file or document its
> inapplicability explicitly under "Exceptions".

## Purpose and scope

This policy governs memory usage: peak memory, steady-state memory,
and unbounded growth; ownership and lifecycle rules for caches,
buffers, queues, collections, subscriptions, handles, and other
retained resources; bounds, eviction, backpressure, cleanup, and
shutdown expectations; leak detection and soak-test methods; and
treatment of large inputs, streaming, batching, and
allocation-sensitive paths.

## Default requirements

* Memory limits, growth expectations, and representative workloads MUST
  be documented when known.
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

* RALPH-FACT: peak_memory_baseline: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: steady_state_memory_baseline: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: cache_lifecycle_policy: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: bounded_accumulator_policy: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: leak_detection_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: soak_test_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: large_input_handling_pattern: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: ci_soak_integration: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT existing memory audits, soak tests, and lifecycle patterns
  before any change that can affect memory.
* PRESERVE stricter existing memory rules; adapt rather than weaken.
* REPLACE every starter placeholder with a verified value.
* DISTINGUISH peak memory, steady-state memory, and unbounded growth
  in every change.
* PREFER existing lifecycle patterns over new collections.

The agent MUST NOT:

* Add an unbounded collection (list / dict / set / deque without
  maxlen) to module-level scope or to instance attributes in
  `__init__` without a documented cap.
* Fabricate a fixed memory budget when the project has not established
  one.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the leak / soak audit.
On regression, the agent MUST report the affected code path, the
regression magnitude, and the cause.

## Exceptions

A documented exception to a memory budget requires a documented
rationale, scope, owner, and review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new cache, queue, or buffer is introduced.
* A bounded accumulator's cap or eviction policy changes.
* A new soak test or leak detector is added.

## Research basis

* publisher: Google SRE Book
  title: "Reliability and the Big Picture"
  http: https://sre.google/sre-book/reliability-and-the-big-picture/
  review date: 2026-07-11

* publisher: Brendan Gregg
  title: "Systems Performance (2nd Edition)"
  http: https://www.brendangregg.com/systems-performance-2nd-edition-book.html
  review date: 2026-07-11

* publisher: Martin Thompson
  title: "Mechanical Sympathy: Memory Hierarchy"
  http: https://mechanical-sympathy.blogspot.com/
  review date: 2026-07-11

* publisher: ACM Queue
  title: "Urban Performance Myths, Revisited"
  http: https://queue.acm.org/detail.cfm?id=2510089
  review date: 2026-07-11

## Ralph markers

* Policy id: `<!-- ralph-policy-id: memory-usage-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` completion comment (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).