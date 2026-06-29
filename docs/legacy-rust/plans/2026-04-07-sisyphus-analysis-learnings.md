# Ralph Workflow Evolution Proposal

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.

## Incorporating Sisyphus Learnings Without Replacing Ralph’s Core Architecture

**Date:** 2026-04-07  
**Status:** Proposed  
**Scope:** Incremental architecture improvements to existing Ralph pipeline

---

## 1) Executive Direction

We should **evolve** Ralph, not replace it.

Ralph already has strong foundations:
- reducer/event-loop orchestration
- explicit phase model
- continuation and retry state
- review/fix loop
- checkpoint persistence

The proposal is to incorporate Sisyphus patterns as **additive control layers**:
1. stronger planning contracts
2. higher-quality completion gating
3. smarter review and retry behavior
4. better progress fidelity

This keeps Ralph’s architecture intact while materially improving unattended reliability.

---

## 2) Current Ralph Baseline (What We Preserve)

Ralph’s current sequential architecture remains:

**Planning → Development → Review/Fix → Commit → FinalValidation/Finalizing**

We preserve:
- phase boundaries
- reducer-driven transitions
- effect orchestration
- checkpoint/resume behavior
- continuation/fallback semantics

We are not proposing a new runtime model or agent framework.

---

## 3) Core Enhancements to Adopt

## A. Intent-Aware Planning Depth (Pre-Planning Gate)

### What to take from Sisyphus
Sisyphus classifies work intent before planning and scales effort by complexity.

### Ralph evolution
Add a lightweight intent gate before plan generation to choose planning strictness:
- Trivial / simple → lean plan
- refactor / architecture / cross-cutting → full plan contract

### Value
Reduces over-planning for small work and under-planning for complex work.

---

## B. Planning as an Execution Contract

### What to take from Sisyphus
Plans explicitly define scope, guardrails, verification expectations, and completion criteria.

### Ralph evolution
Keep PLAN.md, but strengthen it as a contract with:
- IN / OUT scope
- must-have / must-not-have constraints
- deliverable-level acceptance criteria
- review lenses and stop conditions

### Value
Review/Fix can validate against explicit contract rather than inferred intent.

---

## C. QA Scenario Requirement in Review

### What to take from Sisyphus
Each task carries behavior-level verification scenarios (happy + failure paths).

### Ralph evolution
Within existing Review/Fix loop, require behavior checks in addition to code analysis:
- happy-path checks
- failure-path checks
- explicit evidence expectations

### Value
Moves Ralph from “code looked right” to “behavior proven right.”

---

## D. Verified Completion Gate

### What to take from Sisyphus
Completion claims are validated, not trusted.

### Ralph evolution
Ralph already has structured statuses (`completed/partial/failed`, `issues_remain/all_issues_addressed`).
Enhance final acceptance by requiring verification evidence to match status.

### Value
Prevents false-finish states in unattended runs.

---

## E. Multi-Lens Review (Still One Review Phase)

### What to take from Sisyphus
Parallel review perspectives catch different classes of defects.

### Ralph evolution
Keep single Review phase, but make it explicitly cover lenses:
1. plan compliance
2. code quality
3. behavior verification
4. scope drift

### Value
Higher issue recall without introducing a new phase model.

---

## F. Progress Granularity via Work Units

### What to take from Sisyphus
Todo-driven execution provides fine-grained progress and continuation guidance.

### Ralph evolution
Add lightweight work-unit tracking inside iterations (without changing phase graph):
- current work unit
- completed work units
- blocked/remaining units

### Value
Better resumability, better continuation prompts, clearer diagnostics.

---

## G. Adaptive Retry Policy

### What to take from Sisyphus
Use escalating retry behavior and backoff instead of repeating same pattern.

### Ralph evolution
Ralph already has retry counters and continuation state. Extend policy with:
- exponential backoff on repeated same-class failures
- strategy shift trigger after N attempts
- stronger separation of transient vs structural failure modes

### Value
Reduces retry thrash, improves eventual completion rate.

---

## H. “Research Before Commit to Plan”

### What to take from Sisyphus
Gather codebase context before deep planning decisions.

### Ralph evolution
In Planning, explicitly require quick context checks before finalizing PLAN.md:
- existing patterns
- relevant test infrastructure
- integration boundaries

### Value
Higher first-pass plan correctness.

---

## I. Draft-Then-Finalize Planning UX

### What to take from Sisyphus
Use evolving planning drafts to reduce misalignment early.

### Ralph evolution
Preserve PLAN.md final artifact, but allow an intermediate draft representation during planning.

### Value
More robust requirement convergence before execution starts.

---

## J. Harder Stop Conditions for Loop Safety

### What to take from Sisyphus
Explicit completion promises and fail-safe termination conditions reduce infinite loops.

### Ralph evolution
Ralph already includes loop protections and completion markers. Strengthen operator-facing stop semantics by standardizing:
- iteration exhaustion outcomes
- failure exhaustion outcomes
- what qualifies as terminal success vs terminal degraded

### Value
Improved observability and predictability for unattended operation.

---

## K. Parallelization Without Replacing the Phase Architecture

### What to take from Sisyphus
Sisyphus gets speed and reliability from controlled parallel work, especially where tasks are independent and verifiable.

### Ralph evolution
Keep Ralph’s phase graph intact, but add **bounded parallelism inside phases**:

1. **Planning parallel probes**  
   Run independent context collection in parallel (codebase patterns, test infra, dependency constraints), then synthesize into one plan contract.

2. **Development work-unit parallelism**  
   Split a single iteration into independent work units that can run concurrently when they do not conflict (file ownership / dependency boundaries).

3. **Review multi-lens parallel checks**  
   Keep one Review phase, but execute plan-compliance, quality, behavior, and scope checks in parallel, then aggregate findings into one issue set.

4. **Parallel final validation bundle**  
   Run final validation checks concurrently (build, tests, lint, acceptance scenarios), with one deterministic pass/fail gate.

### Guardrails (to keep architecture stable)
- Parallelism is **bounded** (configurable max concurrency)
- Parallel tasks must be **declared independent**
- Aggregation remains deterministic (single reducer-visible result)
- On conflict or uncertainty, system degrades to sequential mode

### Value
Substantial throughput gain and shorter unattended runs without changing Ralph’s core orchestration model.

---

## 4) What We Intentionally Do NOT Import

To avoid architecture churn, we do **not** import:
- a full external multi-agent runtime
- model-coupled orchestration abstractions
- TypeScript-specific harness features (e.g., hashline editing mechanics)

We selectively adopt principles, not framework internals.

---

## 5) Proposed Evolution Roadmap

## Phase 1 — Contract & Review Quality (Lowest Risk, High ROI)
1. Intent-aware planning depth
2. Plan contract strengthening (scope + guardrails + acceptance)
3. QA scenario requirements inside review
4. Multi-lens review criteria

**Outcome:** Better plan/review quality with minimal runtime changes.

## Phase 2 — Completion Confidence & Progress Fidelity
1. Verified completion gate tied to evidence
2. Iteration work-unit tracking
3. Improved continuation messaging from progress state

**Outcome:** Fewer false completions and clearer run-state behavior.

## Phase 3 — Retry Intelligence & Reliability
1. Adaptive backoff tuning
2. Failure-class-based retry strategies
3. Hardened terminal condition signaling

**Outcome:** More stable unattended runs under adverse conditions.

## Phase 4 — Bounded Parallelization (Architecture-Preserving)
1. Parallel planning probes with deterministic synthesis
2. Work-unit level development parallelism (only for independent units)
3. Parallel multi-lens review aggregation
4. Parallel final validation bundle with single acceptance gate

**Outcome:** Faster completion time while preserving phase architecture and deterministic state transitions.

---

## 6) Architecture Fit Statement

This proposal is designed to **improve Ralph’s architecture, not replace it**.

It uses Ralph’s existing primitives:
- phase model
- reducer transitions
- continuation state
- review/fix loop
- checkpoint and termination safeguards

The evolution is primarily about:
- stronger contracts,
- better verification semantics,
- smarter control policies.

---

## 7) Decision Summary

If we adopt only one principle from Sisyphus, it should be this:

> **Treat completion as a verified contract outcome, not a phase exit event.**

Combined with stronger plan contracts and multi-lens review, this gives Ralph significantly better unattended reliability while preserving its architecture.
