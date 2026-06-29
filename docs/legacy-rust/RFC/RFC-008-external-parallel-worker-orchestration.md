# RFC-008: Deterministic Parallel Worker Orchestration

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


**Status**: Draft
**Authors**: Product synthesis from external orchestrator research
**Created**: 2026-03-18

> NOTE: RFCs are historical design documents.
> For canonical architecture details, prefer `../architecture/event-loop-and-reducers.md`, `../architecture/effect-system.md`, and `../architecture/streaming-and-parsers.md`.

---

## Summary

Ralph Workflow should add deterministic parallel worker orchestration to its unattended development pipeline. The system should infer from `PROMPT.md` when work can safely fan out, encode that decision as deterministic Rust-side logic wherever possible, execute bounded parallel workers with explicit branch contracts, and reconcile results back into one coherent run narrative.

This RFC proposes an external orchestrator model. Ralph Workflow owns orchestration semantics above the agent runtime. It does not delegate orchestration to provider-native subagent features such as OpenCode-internal subagents or Claude's tool-use subagent patterns. The goal is consistent, deterministic behavior across every supported drain, plus stronger control over boundaries, workspaces, cleanup, observability, and recovery.

### Naming Clarification

- **Ralph** is the general concept of unattended AI agents running back-to-back, originated by Geoffrey Huntley.
- **Ralph Workflow** is this specific orchestrator: a Rust CLI that implements the Ralph concept with a structured Plan → Develop → Verify cycle, reducer-driven event loop, and deterministic pipeline.

This RFC describes changes to Ralph Workflow, not to the broader Ralph concept.

---

## Context And Problem

Ralph Workflow is built for unattended, deterministic, long-running software delivery. Its core design philosophy is: **make as many decisions deterministic in Rust as possible, and only call on an AI agent when the decision genuinely requires judgment about code.**

Today Ralph Workflow is strong at serial plan → develop → analyze loops, but it lacks a first-class model for parallelizing independent workstreams inside one run.

That gap matters because many real prompts naturally decompose into concurrent lanes:

- parallel research across multiple systems
- independent edits in separate modules
- verification across multiple branches of work
- contract-first design followed by isolated implementation

Without a productized orchestration model, Ralph Workflow either leaves obvious speed on the table or risks ad hoc multi-agent behavior that is hard to reason about, hard to recover from, and impossible to make deterministic.

The problem is not "how do we spawn more agents." The problem is how to add parallelism while preserving the properties that make Ralph Workflow different from interactive agent orchestrators:

- unattended execution without human babysitting
- deterministic decision-making encoded in Rust, not delegated to models
- branch isolation and prompt boundaries
- resume and recovery without losing successful work
- provider-agnostic behavior across agent backends
- explicit cleanup and lifecycle management

This RFC also recognizes a second issue: `plan.xsd` is too weak for advanced developer-agent execution. It was created early, before we understood what the planning contract truly needed. It should be improved as part of this feature so it can express parallel worker instructions, workspace strategy, and stronger developer-facing execution guidance overall.

---

## What Makes Ralph Workflow Different

Most AI orchestrators delegate orchestration decisions to models. LangGraph builds graphs where LLM calls decide routing. CrewAI uses role-play and manager agents. OpenAI Agents SDK uses conversation handoffs decided by the model. OhMyAgent uses shell-level spawning with file-based coordination.

Ralph Workflow takes a fundamentally different approach: **the orchestrator is a deterministic Rust state machine.** Orchestration logic is pure functions over typed state. Reducers are pure. Effects are isolated. The event loop is explicit and replayable. AI agents are called only for tasks that genuinely require reasoning about code — planning, development, analysis — not for routing, scheduling, retry, recovery, or lifecycle decisions.

This means Ralph Workflow can:

- make orchestration decisions without consuming model tokens
- guarantee the same orchestration behavior regardless of which model backs a drain
- replay, checkpoint, and resume without depending on model memory or session state
- audit every orchestration decision from the event log without reverse-engineering model reasoning
- extend orchestration intelligence with traditional ML models or rule-based systems in the future, not just LLMs

Parallel worker orchestration should follow the same principle. The decision to fan out, the branch contracts, the workspace strategy, the merge/reconciliation rules, the cleanup lifecycle — all of these should be deterministic Rust-side logic. The AI agent's job is to do the work inside each branch, not to manage the orchestration around it.

---

## Goals

1. Add parallel worker execution to Ralph Workflow's unattended pipeline.
2. Keep orchestration decisions deterministic in Rust wherever possible; use AI only for content-producing work inside branches.
3. Infer parallelism opportunities semantically from `PROMPT.md` during the planning phase.
4. Define explicit branch contracts for prompt scope, outputs, constraints, and ownership.
5. Expand `plan.xsd` so the planning contract better serves developer agents overall, not just parallel work.
6. Define workspace isolation rules and deterministic cleanup rules for Windows, macOS, and Linux.
7. Preserve successful branch work across partial failures and resumes.
8. Make branch execution observable without turning Ralph Workflow into an interactive coordinator.

---

## Non-Goals

1. Building a free-form swarm where agents autonomously spawn unbounded descendants.
2. Making provider-native subagents the primary product model.
3. Delegating orchestration routing, scheduling, or recovery decisions to LLMs.
4. Requiring users to manually author orchestration directives for routine cases.
5. Designing a human-in-the-loop product that depends on real-time approvals to proceed.
6. Exposing nested worktree or recursive workspace mechanics as a user-facing concept.

---

## Product Principles

### 1. Deterministic First, AI Where It Counts

Ralph Workflow should encode orchestration choices — when to fan out, how to assign branches, when to reconcile, how to recover — as deterministic logic in Rust. AI agents do the creative work inside branches. They do not manage the workflow around it.

### 2. Ralph Workflow Owns Orchestration

Parallel orchestration is a Ralph Workflow capability, not an OpenCode feature, not a Claude feature, and not a provider-specific extension. Ralph Workflow decides when to branch, what each branch does, how state is tracked, and how results merge.

### 3. Unattended First

If a user is absent for the entire run, the system should still make progress. Ambiguity handling, throttling, failures, and cleanup must all follow unattended defaults without blocking on human input.

### 4. Bounded, Not Swarmy

Parallelism should be explicit, bounded, and inspectable. Ralph Workflow should support a small number of meaningful branches, not unconstrained recursive worker spawning.

### 5. Contract Before Concurrency

Branches should only run in parallel when their responsibilities, outputs, and boundaries are stable enough to avoid hidden overlap. The contract is established before execution, not discovered during it.

### 6. Progress Must Survive Failure

If one branch fails, completed branches should remain usable. Recovery should start from structured retained state, not from scratch.

### 7. Cleanup Is Part Of Correctness

Temporary branch workspaces, checkpoints, and orchestration artifacts need deterministic lifecycle rules. Cleanup is not an optional postscript; it is part of the correctness contract.

---

## Decision

Ralph Workflow should implement deterministic parallel worker orchestration with two primary modes:

### 1. Bounded Fan-Out

- Used when planning identifies clearly independent workstreams.
- Ralph Workflow creates a fixed set of branch work packets with explicit ownership and merge contracts.
- Branch count, assignment, and reconciliation rules are determined by Rust-side logic from the structured plan.
- Branches run in parallel through Ralph-managed workers.

### 2. Supervisor-Led Delegation

- Used when work is decomposable but not fully known upfront.
- Ralph Workflow still owns the orchestration model, but one structured supervisory pass decides which bounded branches to issue next.
- This remains a Ralph Workflow concept expressed in the reducer/event model, not a provider-native subagent graph.

In both modes, Ralph Workflow launches and manages headless agent workers directly. It does not expose provider-native subagent semantics as the product surface.

---

## Why External Orchestration, Not Internal Provider Orchestration

This proposal intentionally differentiates Ralph Workflow from an internal OpenCode or Claude orchestrator.

### What "External" Means

Ralph Workflow sits above the agent runtime. It manages process lifecycle, workspace setup, prompt delivery, output collection, state tracking, and recovery. The agent inside each worker is a content producer, not an orchestration participant.

### What "Internal" Would Mean

An internal orchestrator would delegate branch planning, routing, and coordination to the model itself — for example, OpenCode's native subagent system or Claude's tool-use patterns for spawning child agents.

### Why External Wins For Ralph Workflow

| Dimension | External (Ralph Workflow owns it) | Internal (provider owns it) |
|-----------|-----------------------------------|----------------------------|
| Determinism | Orchestration is pure Rust logic, auditable and replayable | Orchestration depends on model reasoning, non-deterministic |
| Provider portability | Same behavior across OpenCode, Claude, future drains | Behavior varies per provider's subagent capabilities |
| Recovery | Checkpoint/resume at Ralph Workflow level with full state | Recovery depends on provider session persistence |
| Observability | Event log captures every orchestration decision | Model reasoning is opaque unless provider exposes traces |
| Workspace control | Ralph Workflow manages isolation, cleanup, lifecycle | Provider may not expose workspace semantics at all |
| Cost | Orchestration decisions consume zero model tokens | Every routing/scheduling decision costs tokens |
| Future extensibility | Can add traditional ML, rule-based systems, heuristics | Locked to LLM-based decision-making |

### When Internal Might Be Used Later

Provider-native subagents could be used as an internal optimization if they can faithfully satisfy the same Ralph Workflow contract. But they should never define user-visible behavior, branch semantics, or lifecycle rules.

---

## Research Synthesis: What To Copy, Adapt, And Avoid

### OhMyAgent

| Verdict | Feature | Rationale |
|---------|---------|-----------|
| **Copy** | Session-based grouping for related worker runs | Clean coordination primitive |
| **Copy** | Contract-first parallel execution | Prevents integration failures by locking interfaces before coding |
| **Copy** | Explicit done criteria for automation | Clear completion signals for unattended runs |
| **Copy** | Workspace-aware worker execution | Reduces merge conflicts in multi-domain work |
| **Adapt** | Two-layer prompt packaging (lightweight top + on-demand depth) | Token-efficient; adapt to Ralph Workflow's plan.xsd model |
| **Adapt** | Dashboard-style visibility | Integrate into Ralph Workflow's existing log/trace system, not a separate product |
| **Avoid** | Shell-native orchestration (`&`, `wait`) | Fragile; Ralph Workflow should manage workers in Rust |
| **Avoid** | File-memory as main orchestration backbone | Ralph Workflow uses typed reducer state, not filesystem conventions |

### LangGraph

| Verdict | Feature | Rationale |
|---------|---------|-----------|
| **Copy** | Checkpoint, resume, and fork semantics | Directly maps to Ralph Workflow's existing checkpoint architecture |
| **Copy** | Explicit fan-out and fan-in branching model | Clean parallel primitive |
| **Copy** | Reducer-friendly handling of parallel state updates | Append semantics prevent branch overwrites |
| **Adapt** | Typed state and merge semantics | Integrate into Ralph Workflow's own reducer/event types |
| **Avoid** | Graph-programming mental model as user-facing surface | Users should not need to think in DAGs |

### CrewAI

| Verdict | Feature | Rationale |
|---------|---------|-----------|
| **Copy** | Clear distinction between collaboration and workflow orchestration | Useful conceptual separation |
| **Copy** | Explicit manager-led mode for hierarchical coordination | Maps to supervisor-led delegation |
| **Adapt** | Built-in tracing and run visibility | Good ideas; adapt to Ralph Workflow's event loop trace |
| **Avoid** | Role-play framing as the primary product metaphor | Ralph Workflow should stay operational and deterministic |

### OpenAI Agents SDK And Microsoft Agent Framework

| Verdict | Feature | Rationale |
|---------|---------|-----------|
| **Copy** | Explicit handoff/resume concepts | Useful for branch lifecycle |
| **Copy** | Strong observability and tracing expectations | Aligns with Ralph Workflow's event log philosophy |
| **Adapt** | Lifecycle-aware interruptions | Convert to unattended-safe defaults |
| **Avoid** | Conversation handoff as the primary orchestration primitive | Not suited for unattended coding runs |
| **Avoid** | Model-driven routing decisions | Ralph Workflow should route deterministically |

---

## Proposed Product Model

### 1. Semantic Parallelism Detection

During the planning phase, Ralph Workflow should identify whether a prompt warrants parallel workers.

Signals include:

- multiple independent deliverables in `PROMPT.md`
- separable subsystems or module boundaries
- naturally parallel research or validation lanes
- contract-first tasks followed by isolated implementation

The planner (AI agent) identifies candidate branches. Ralph Workflow (Rust logic) validates independence, assigns workspace strategy, and decides the orchestration mode. If semantic independence is weak or ambiguous, Ralph Workflow defaults to serial execution.

### 2. Structured Planning Contract

`plan.xsd` should be expanded so the planner can emit structured branch metadata that Ralph Workflow's Rust-side logic can consume deterministically:

- whether parallel execution is recommended
- orchestration mode (`serial`, `bounded_fan_out`, `supervisor_led`)
- branch identities and responsibilities
- branch prompt contracts (structured, not freeform)
- workspace strategy per branch
- merge/reconciliation expectations
- cleanup and lifecycle expectations

This is not just a subagent addition. The overall planning contract should become more useful for developer agents even when no parallel workers are used.

### 3. Branch Prompt Contract

Each branch receives a structured work packet:

- branch goal
- branch scope (files, modules, subsystems)
- allowed context
- forbidden actions or out-of-scope areas
- expected outputs
- verification expectations
- merge assumptions

Ralph Workflow constructs these deterministically from the structured plan. The AI agent inside each branch receives a focused, scoped prompt — not a copy of the full run prompt.

### 4. Workspace Strategy

Ralph Workflow should choose one of the following deterministically:

- **shared codebase view**: for safe non-conflicting work (e.g., edits to completely separate modules)
- **isolated branch workspace**: for overlapping or risky edits (e.g., separate git worktrees)

The product should not expose "worktree of worktree" or nested workspace mechanics. Ralph Workflow defines supported isolation strategies and selects among them based on structured plan metadata.

### 5. Cleanup And Lifecycle

If temporary branch workspaces or artifacts are created, Ralph Workflow defines deterministic lifecycle behavior for:

- normal completion → merge results, remove workspace
- branch failure → retain state for recovery, mark failed, clean on resolution
- cancellation → clean all branch artifacts
- interrupted runs → retain enough state to resume
- resume after crash → validate retained state, clean stale artifacts

Cleanup rules are platform-aware:

- **Linux**: standard filesystem cleanup; no special locking concerns
- **macOS**: account for `.DS_Store`, extended attributes, and Spotlight indexing delays
- **Windows**: account for file locks, delayed deletes, long path limits, and permission inheritance

### 6. Observability

Ralph Workflow exposes branch state through its existing event loop trace and log infrastructure:

- run-level orchestration mode in the event log
- branch status events (`planned`, `running`, `completed`, `failed`, `reconciled`)
- branch ownership, outputs, and failure reasons
- retained successful branches after partial failure
- cleanup and resume state

This is operational visibility through existing log and trace mechanisms, not an interactive dashboard dependency.

---

## No-Training Decision Algorithms For Parallelization

Ralph Workflow does not need supervised ML to ship a credible first version of parallel worker orchestration. The most realistic path is a no-training decision stack that combines deterministic rules with graph-based grouping.

### 1. Hard Constraints First

Before any scoring, Ralph Workflow should identify work items that must not run in parallel.

Examples:

- likely edits to the same files
- shared contracts, schemas, or API boundaries that are still unstable
- explicit producer-consumer dependencies between plan items
- shared stateful resources such as migrations or global configuration
- verification surfaces that must be evaluated together

These constraints define the first dependency and conflict graph.

### 2. Pairwise Compatibility Scoring

For work items that are not ruled out by hard constraints, Ralph Workflow should compute a pairwise compatibility score.

Useful dimensions include:

- file overlap risk
- module overlap risk
- contract coupling
- sequencing dependency strength
- verification coupling
- expected merge difficulty
- confidence that scope is isolated

This produces a practical `parallelizability score` without any model training.

### 3. Graph Construction

Represent the plan as a graph:

- nodes = candidate work items or plan units
- blocking edges = cannot run in parallel
- weighted edges = degree of safe parallel compatibility

This is a better fit than naive clustering because the problem is fundamentally about dependencies and conflicts, not just similarity in feature space.

### 4. Graph Algorithms To Divide Work

Ralph Workflow can then use no-training graph algorithms to derive branch structure:

- **connected components** for hard conflict/dependency groups
- **topological layering** for ordering groups with dependencies
- **community detection** such as Louvain or Leiden for soft grouping inside independent regions
- **balanced graph partitioning heuristics** if branch size balancing becomes important

This gives Ralph Workflow a practical stack for dividing work and deciding what can safely run together.

### 5. Recommended Adoption Path

The recommended sequence is:

1. hard constraints
2. pairwise weighted compatibility scoring
3. graph construction
4. connected components plus topological sorting
5. optional community detection for better grouping once the basics work

In short:

```text
rules -> dependency/conflict graph -> compatibility scores -> graph partitioning/community detection -> bounded branch selection
```

This is the most realistic path from zero parallelization to a usable first feature.

### 6. Why Not Start With GNNs

Graph Neural Networks are appealing in theory, but they require training data Ralph Workflow does not yet have: historical tasks, dependency labels, merge-conflict outcomes, branch success/failure, and reliable ground truth for what "should have been parallelized."

Without that dataset, a GNN is likely to delay the feature rather than accelerate it.

### 7. Where Unsupervised Or Traditional ML May Fit Later

Once Ralph Workflow has real execution history, it may add:

- unsupervised clustering or community-detection refinements for branch grouping
- heuristic or statistical scoring for complexity estimation and resource allocation
- traditional ML classifiers for branch independence confidence or merge-conflict prediction

But the first useful version should come from rules plus graph algorithms, not from trained models.

---

## Future Expansion: Beyond LLMs

Because Ralph Workflow owns orchestration in deterministic Rust rather than delegating it to models, the system can incorporate additional decision layers over time:

- **Traditional ML models** for classification, routing, scoring, or prioritization (e.g., predicting branch independence confidence, estimating branch complexity for resource allocation, detecting likely merge conflicts before execution)
- **Rule-based systems** for compliance, policy enforcement, or domain-specific constraints
- **Heuristic engines** for cost optimization, scheduling, or resource management

LLMs remain the right tool for content-producing work: planning, coding, analysis. But orchestration intelligence does not need to be LLM-shaped. Ralph Workflow's architecture makes it possible to use the right computation layer for each decision.

---

## Proposed User Experience

From the user's perspective:

1. They write `PROMPT.md` in product language. They do not specify orchestration mechanics.
2. Ralph Workflow plans the work. If the plan identifies independent branches, the structured plan says so.
3. Ralph Workflow's Rust-side logic validates the branch structure, assigns workspace strategy, and chooses orchestration mode — all deterministically.
4. Ralph Workflow launches bounded parallel workers unattended.
5. The user can inspect branch status from logs and traces if they want, but the run never depends on them.
6. Ralph Workflow reconciles branch outputs into one coherent result deterministically.
7. The experience feels faster and more capable without feeling interactive, swarmy, or unpredictable.

---

## Rollout Plan

### Phase 1: Planning Contract Upgrade

- Expand `plan.xsd` for richer developer-agent guidance overall
- Add structured branch metadata support
- Validate that planners can emit branch recommendations

### Phase 2: Serial-Compatible Branch Validation

- Ralph Workflow validates branch contracts from the plan
- Execute branches serially at first to prove contracts work
- Validate merge/reconciliation semantics end-to-end

### Phase 3: Bounded Fan-Out Execution

- Launch Ralph-managed headless workers for independent branches
- Add branch status events to the event loop
- Support retained progress across partial failure

### Phase 4: Workspace Isolation And Cleanup Hardening

- Add supported workspace isolation strategies (shared view, isolated worktree)
- Add deterministic platform-aware cleanup for Windows, macOS, and Linux
- Validate resume and interrupted-run recovery with branch state

### Phase 5: Supervisor-Led Delegation

- Add bounded dynamic delegation mode
- Preserve the same deterministic Ralph Workflow orchestration contract

---

## Success Criteria

1. Ralph Workflow can infer from `PROMPT.md` whether parallel workers are warranted, with the orchestration decision made in Rust, not by the model.
2. The planner can emit explicit structured branch instructions in `plan.xsd`.
3. Developer agents receive materially better structured plans overall, even for serial work.
4. Ralph Workflow supports at least bounded fan-out with retained progress across partial failure.
5. Branch prompt boundaries are explicit, structured, and enforced by Rust-side logic.
6. Workspace strategy is chosen deterministically per branch based on structured plan metadata.
7. Temporary workspace cleanup has deterministic cross-platform rules for Windows, macOS, and Linux.
8. User-facing behavior is consistent across providers, including those without native subagents.
9. Orchestration decisions consume zero model tokens.

---

## Risks And Mitigations

| Risk | Why It Matters | Mitigation |
|------|----------------|------------|
| Over-eager parallelism | False independence creates branch conflicts | Bias toward serial unless structured plan strongly indicates independence |
| Provider divergence | OpenCode and Claude behave differently under parallel load | Keep all orchestration semantics in Rust |
| Weak branch prompts | Branches drift, duplicate work, or exceed scope | Enforce structured branch prompt contract, validated before execution |
| Workspace collisions | Parallel edits corrupt shared state | Choose stronger isolation when plan metadata indicates file overlap risk |
| Cleanup failures | Temp workspaces accumulate or become unrecoverable | Deterministic lifecycle rules with platform-aware cleanup |
| Schema bloat in plan.xsd | Planning contract becomes harder to use | Expand only around core developer-agent needs; keep schema focused |
| Product complexity | Users lose the simplicity of Ralph Workflow | Keep UX automatic and outcome-focused; orchestration is invisible |
| Deterministic rigidity | Rust-side rules miss edge cases a model would catch | Design rules to be conservative (default serial) and add ML/heuristic layers later |

---

## Alternatives Considered

### A. Keep Ralph Workflow Fully Serial

Rejected. Leaves major performance and capability gains untapped for naturally decomposable work.

### B. Use Provider-Native Subagents As The Product Model

Rejected. Creates provider lock-in, inconsistent semantics across drains, and delegates orchestration decisions to non-deterministic model reasoning.

### C. Build An Unbounded Autonomous Swarm

Rejected. Conflicts with Ralph Workflow's unattended, deterministic product identity.

### D. Require User-Specified Orchestration Directives In PROMPT.md

Rejected. The product should infer orchestration from user goals, not require users to become workflow programmers.

### E. Delegate Orchestration Routing To LLMs

Rejected. Orchestration routing, scheduling, retry, and recovery are deterministic problems. Delegating them to models wastes tokens, introduces non-determinism, and makes the system harder to audit and replay.

---

## Open Questions

1. What is the minimum viable `plan.xsd` expansion that materially improves developer-agent execution without overfitting?
2. Which branch states should be first-class in Ralph Workflow's reducer/event model?
3. Should branch reconciliation happen in planning, development, analysis, or a dedicated synthesis phase?
4. What is the safest default workspace model for code-editing branches: shared view or isolated worktree?
5. How much branch-level visibility should appear in the normal CLI versus logs and trace outputs?
6. What traditional ML or heuristic approaches are most promising for branch independence scoring, complexity estimation, or conflict prediction?
7. When, if ever, should Ralph Workflow internally use provider-native subagents as an optimization behind the same deterministic external contract?

---

## References

- OhMyAgent official docs: `https://first-fluke.github.io/oh-my-agent/`
- OhMyAgent repository: `https://github.com/first-fluke/oh-my-agent`
- LangGraph docs: `https://docs.langchain.com/oss/python/langgraph/`
- CrewAI docs: `https://docs.crewai.com/`
- OpenAI Agents SDK docs: `https://openai.github.io/openai-agents-python/`
- Microsoft Agent Framework overview: `https://learn.microsoft.com/en-us/agent-framework/overview/`
- Ralph Workflow design philosophy: `../../README.md` (Design Philosophy section)
- Ralph Workflow architecture: `../architecture/event-loop-and-reducers.md`
- Ralph Workflow architecture: `../architecture/effect-system.md`
- Ralph Workflow architecture: `../architecture/streaming-and-parsers.md`
