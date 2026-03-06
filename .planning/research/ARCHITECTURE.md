# Architecture Research

**Domain:** GLM-agent planning workflow systems (execution-ready planning pipelines)
**Researched:** 2026-03-05
**Confidence:** MEDIUM-HIGH

## Standard Architecture

### System Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Ingress Layer                                                                │
│  API/CLI Trigger ──> Session/Auth ──> Request Validator                      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ validated planning request
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ Orchestration Layer                                                           │
│  Workflow Orchestrator (state graph / durable workflow)                       │
│  ├── Planner Agent                                                            │
│  ├── Decomposer / Task Graph Builder                                          │
│  ├── Executor Router (tools + specialized agents)                             │
│  ├── Reviewer / Critic / Guardrails                                           │
│  └── Human Approval Gate (interrupt/resume)                                   │
└───────────────┬──────────────────────────────┬───────────────────────────────┘
                │ reads/writes state            │ dispatches tasks/messages
┌───────────────▼──────────────────────┐  ┌────▼───────────────────────────────┐
│ State + Memory Layer                  │  │ Agent Runtime / Messaging Layer    │
│  - Run state/checkpoints              │  │  - Topic routing / queueing         │
│  - Planning artifacts                 │  │  - Agent lifecycle                  │
│  - Prompt/decision traces             │  │  - Delivery + retries               │
└───────────────┬──────────────────────┘  └────┬───────────────────────────────┘
                │                               │
┌───────────────▼───────────────────────────────▼──────────────────────────────┐
│ Integration Layer                                                             │
│  Model Gateway (GLM providers) | Tool Adapters (fs/git/http/test runners)    │
│  Observability (traces/metrics/logs) | Policy/Safety enforcement              │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Ingress (API/CLI) | Accept planning request, enforce schema, issue run id | FastAPI/Express/CLI command + JSON schema validation |
| Workflow Orchestrator | Owns control flow, branching, retries, resume points | LangGraph `State/Nodes/Edges` or Temporal Workflow definitions |
| Planner Agent | Produces initial plan and milestone decomposition | Single high-capability GLM call with structured output |
| Decomposer / Task Graph Builder | Converts plan into dependency-aware task DAG | Deterministic transformer + validation rules |
| Executor Router | Routes tasks to specialist agents/tools | Rule-based router or classifier + tool registry |
| Specialist Agents | Execute focused subtasks (research, code, review) | Routed agents with narrow prompts and strict contracts |
| Reviewer / Critic | Evaluate outputs against quality gates | LLM judge + deterministic checks (schema/tests/lints) |
| Human Approval Gate | Pause high-risk transitions and capture approvals | Interrupt + resume checkpoints |
| Agent Runtime / Messaging | Deliver inter-agent messages and manage subscriptions | AutoGen runtime topic/subscription model |
| State + Artifact Store | Persist run state, checkpoints, artifacts, and provenance | Postgres + object store + vector index (optional) |
| Model Gateway | Unify provider APIs, budgets, fallback and rate-limit policy | Provider abstraction layer (OpenAI/Anthropic/local GLM) |
| Observability | End-to-end traceability per run/step/artifact | OpenTelemetry + app logs + run timeline UI |

## Recommended Project Structure

```text
src/
├── ingress/                  # API/CLI entrypoints and request validation
│   ├── api/                  # HTTP handlers
│   └── cli/                  # local command wrappers
├── orchestrator/             # workflow graph and state machine
│   ├── graph/                # nodes, edges, route policies
│   ├── checkpoints/          # resume/persist adapters
│   └── policies/             # retry, timeout, escalation
├── agents/                   # planner/executor/reviewer implementations
│   ├── planner/
│   ├── specialists/
│   └── reviewer/
├── runtime/                  # message bus, routing, agent lifecycle
├── tools/                    # tool adapters (git/fs/http/tests)
├── memory/                   # state store, artifact store, retrieval
├── guardrails/               # policy, validation, safety checks
├── observability/            # tracing, metrics, structured logs
└── shared/                   # contracts, schemas, ids, errors
```

### Structure Rationale

- **`orchestrator/` is isolated from `agents/`:** control-flow decisions stay deterministic even when agent outputs vary.
- **`runtime/` is isolated from `agents/`:** message transport/lifecycle can evolve without rewriting agent logic.
- **`memory/` is isolated from `orchestrator/`:** persistence backends can change (sqlite -> postgres) without graph rewrites.
- **`guardrails/` sits cross-cutting:** validation/policy gates are reusable before and after each major phase.

## Architectural Patterns

### Pattern 1: State-Graph Orchestration (Primary)

**What:** Represent planning workflow as explicit state + node functions + conditional edges.
**When to use:** Always for GLM planning systems that require resumability and deterministic phase gates.
**Trade-offs:** More upfront modeling work, but much lower operational ambiguity than ad hoc agent loops.

**Example:**
```typescript
type PlanState = {
  request: PlanningInput;
  plan?: DraftPlan;
  tasks?: TaskDag;
  review?: ReviewResult;
  status: "drafting" | "decomposed" | "reviewed" | "approved" | "failed";
};

// nodes do work, edges decide next step
START -> planner -> decomposer -> reviewer
reviewer --(passes)--> approval_gate --(approved)--> END
reviewer --(fails)--> planner
```

### Pattern 2: Decoupled Agent Runtime + Topic Routing

**What:** Keep agent logic separate from message transport; use typed topics/channels for each stage.
**When to use:** Multi-agent pipelines where specialists may scale independently or run distributed.
**Trade-offs:** Adds routing complexity, but prevents tight coupling and supports parallel workers.

**Example:**
```typescript
topic.plan_draft -> DecomposerAgent
topic.task_graph -> ExecutorRouter
topic.execution_result -> ReviewerAgent
topic.review_failed -> PlannerAgent
```

### Pattern 3: Durable Workflow + Side-Effect Boundary

**What:** Keep orchestration deterministic; move non-deterministic operations (API/tool/fs) to activity/tool workers with retries.
**When to use:** Long-running planning runs with external tools or intermittent provider failures.
**Trade-offs:** Requires stricter API boundaries, but dramatically improves recovery and replay safety.

## Data Flow

### Request Flow

```text
[User/API request]
    -> [Validator]
    -> [Orchestrator: create run state]
    -> [Planner Agent]
    -> [Task Graph Builder]
    -> [Executor Router -> Specialist Agents/Tools]
    -> [Reviewer + deterministic checks]
    -> [Approval Gate (human if needed)]
    -> [Artifact Writer (.planning/*)]
    -> [Response + run summary]
```

### State and Message Flow

```text
State Store <-> Orchestrator <-> Agent Runtime <-> Agents/Tools
     ^              |                |
     |              v                v
Artifact Store <- Reviewer ------ Observability
```

### Key Data Flows (Direction Explicit)

1. **Planning flow (forward):** Request -> Planner -> Decomposer -> Executor -> Reviewer -> Output artifacts.
2. **Correction flow (feedback):** Reviewer failure -> Planner/Decomposer with structured critique -> revised draft.
3. **Control flow (governance):** Policy engine -> Approval gate decisions -> Orchestrator state transitions.
4. **Recovery flow (durability):** Checkpoint store -> Orchestrator replay/resume -> pending node re-execution.

## Suggested Build Order (Dependency-Driven)

1. **Contracts + State Model**
   Define run ids, step schemas, artifact schema, and status enums first.
2. **Orchestrator Skeleton**
   Implement graph/state machine with stub nodes and deterministic transitions.
3. **Persistence + Checkpointing**
   Add durable run state before adding complex agent logic.
4. **Planner + Decomposer Agents**
   Introduce core planning outputs and DAG generation.
5. **Executor Router + Tool Boundary**
   Add specialist execution paths and side-effect isolation.
6. **Reviewer + Guardrails + Feedback Loop**
   Enforce quality gates and reroute failures to earlier nodes.
7. **Human Approval + Interrupt/Resume**
   Add manual gates for risky transitions.
8. **Observability + Cost/Latency Controls**
   Instrument traces/metrics and set operational budgets.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 0-1k runs/month | Single service + single orchestrator worker + Postgres is sufficient |
| 1k-100k runs/month | Split runtime workers by task type; add queue backpressure and per-provider rate controls |
| 100k+ runs/month | Separate orchestrator from execution workers, shard run state, and add multi-region failover |

### Scaling Priorities

1. **First bottleneck:** Model/tool latency variance; solve with async orchestration, bounded parallelism, and retries.
2. **Second bottleneck:** State/history growth; solve with checkpoint compaction, archive policy, and run TTLs.

## Anti-Patterns

### Anti-Pattern 1: Monolithic "super-agent" for all planning stages

**What people do:** One prompt handles planning, decomposition, execution, and review.
**Why it's wrong:** No clear failure boundary, low debuggability, and weak reproducibility.
**Do this instead:** Separate planner/decomposer/executor/reviewer with explicit contracts.

### Anti-Pattern 2: No durable state between steps

**What people do:** Keep run state only in memory/chat history.
**Why it's wrong:** Crashes lose progress; no replay/audit trail; hard to enforce approvals.
**Do this instead:** Persist checkpoints and artifact versions at each phase boundary.

### Anti-Pattern 3: Let workflow code perform external side effects directly

**What people do:** Orchestrator node performs network/fs mutations inline.
**Why it's wrong:** Breaks deterministic replay and complicates retry semantics.
**Do this instead:** Route side effects through activity/tool workers with idempotency keys.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| GLM providers | Model gateway abstraction | Centralize retries, fallback, token/cost accounting |
| Tool executors (git/fs/test/http) | Isolated worker adapters | Enforce sandboxing + timeout + idempotency |
| Persistence (SQL/object store) | Repository interfaces | Version artifacts and retain full provenance |
| Telemetry backend | OpenTelemetry exporters | Correlate run id -> node id -> tool call |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| ingress <-> orchestrator | synchronous API call | returns run id immediately, execution async optional |
| orchestrator <-> runtime | typed commands/events | orchestrator owns transitions; runtime owns delivery |
| runtime <-> agents | topic/message contracts | agents cannot mutate global state directly |
| reviewer <-> orchestrator | structured verdict object | include failure reason + remediation hint |

## Sources

- LangGraph overview (official): https://docs.langchain.com/oss/python/langgraph/overview (durable execution, HITL, memory) [HIGH]
- LangGraph Graph API concepts via Context7: https://docs.langchain.com/oss/python/langgraph/graph-api (state/nodes/edges) [HIGH]
- LangChain multi-agent patterns via Context7: https://docs.langchain.com/oss/python/langchain/multi-agent/index (subagents, handoffs, router, custom workflow) [HIGH]
- AutoGen Core sequential workflow (official): https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/sequential-workflow.html (topic/subscription message pipeline) [HIGH]
- AutoGen agent runtime concepts via Context7: https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/agent-and-agent-runtime (runtime/agent separation) [HIGH]
- Temporal workflow execution (official): https://docs.temporal.io/workflow-execution (durability, replay, signals) [HIGH]
- Temporal workflow/activity boundary + retries via Context7: https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/temporal-sdks.mdx [HIGH]

## Research Gaps / Validation Flags

- Broad ecosystem web search could not be executed in this environment (Brave API key unavailable), so this architecture is grounded in authoritative framework docs rather than popularity trend data.
- If the implementation target must be one framework (LangGraph vs AutoGen vs Temporal), run a dedicated comparison pass before roadmap lock.

---
*Architecture research for: GLM-agent planning workflow systems*
*Researched: 2026-03-05*
