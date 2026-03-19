# RFC-009: MCP-Style Agent-Orchestrator Communication

**RFC Number**: RFC-009
**Title**: MCP-Style Agent-Orchestrator Communication
**Status**: Draft
**Author**: Product synthesis
**Created**: 2026-03-19

> NOTE: RFCs are historical design documents.
> For canonical architecture details, prefer `../architecture/event-loop-and-reducers.md`, `../architecture/effect-system.md`, `../architecture/agents-and-prompts.md`, and `../architecture/streaming-and-parsers.md`.

---

## Summary

Ralph Workflow should move away from today's hybrid communication model where agents write XSD-validated XML artifacts for phase handoff, stream provider-specific NDJSON for runtime output, and still retain broad ambient access to the local environment. That model has served Ralph well for deterministic phase transitions, but it is too permissive at the runtime boundary and too fragmented for a future with stronger policy enforcement, safer parallelism, and clearer auditability.

This RFC explores adopting an MCP-style communication contract between agents and Ralph Workflow. The recommendation is not to replace Ralph's reducer-driven orchestrator with provider-native agent frameworks. Instead, Ralph should remain the deterministic host and expose a brokered, typed control surface that agents use for tool access, approvals, artifact submission, and status reporting. V1 should focus on orchestrator-mediated tool calls, explicit capabilities, session-scoped permissions, and structured observability.

---

## Context And Problem

Ralph's current communication model is split across three separate mechanisms:

1. **Phase artifacts** are passed through XSD-validated XML written into `.agent/tmp/*.xml`.
2. **Agent runtime output** is parsed from provider-specific NDJSON streams.
3. **Environment access** is largely ambient, with safety enforced through prompts, wrappers, hooks, and selective interception.

This creates real product and platform problems:

- **Security posture is uneven.** Ralph can validate final XML artifacts, but it has weaker control over what the agent attempted before producing them.
- **Policy enforcement is indirect.** Safety rules for git, file writes, and shell usage are spread across prompts, wrappers, hooks, and backend-specific behavior.
- **Parallelism is harder to productize.** When multiple agents run concurrently, ambient environment access makes it difficult to guarantee bounded authority and clear ownership.
- **Observability is fragmented.** XML artifacts, process logs, JSON streams, and reducer events each tell part of the story, but there is no single protocol-level audit trail of what the agent requested and what Ralph allowed.
- **Backend portability is expensive.** Each agent CLI has its own output quirks, session semantics, and tool behavior, which increases compatibility work.

The deeper issue is not only output formatting. It is that Ralph does not yet own a single structured runtime contract for agent interaction.

---

## Current State

Today's architecture is already strong at deterministic orchestration:

- Ralph is a reducer-driven Rust state machine.
- Orchestration decisions are encoded in typed state, events, and effects.
- Agent outputs are validated before becoming trusted pipeline inputs.
- Agent-to-agent communication is not direct; Ralph mediates phase transitions.

But the runtime interface is still mixed:

| Boundary | Current mechanism | Strength | Limitation |
|----------|-------------------|----------|------------|
| Agent -> orchestrator artifact handoff | XSD-validated XML | Deterministic, strict, phase-aware | Narrow and phase-specific |
| Agent -> orchestrator live output | Provider NDJSON parsing | Good UX and streaming | Provider-specific, not policy-oriented |
| Agent -> environment actions | Ambient tools / shell / local access | Flexible | Hard to constrain logically |
| Orchestrator -> agent instructions | Prompt text + CLI args + env | Simple | Weak capability contract |

This is why Ralph can reliably determine whether a `plan.xml` is valid, but cannot express higher-order guarantees such as:

- this agent may read files but not modify tracked code
- this agent may request a git diff but never a git commit
- this agent may spawn at most three bounded branch workers
- this tool call requires policy approval before execution

---

## Why This Matters Now

This work becomes more urgent as Ralph expands into:

- deterministic parallel worker orchestration
- stronger no-edit and least-privilege drain models
- richer multi-agent flows with clearer branch ownership
- broader backend support through OpenCode and future runtimes
- better enterprise trust, audit, and policy expectations

Without a unified runtime contract, every new orchestration feature increases safety complexity at the edges.

---

## Goals

1. Give Ralph one coherent runtime communication model between agents and the orchestrator.
2. Make tool access explicit, typed, and policy-enforceable before execution.
3. Preserve Ralph's core architecture: deterministic orchestration in Rust, not delegated to the model.
4. Improve safety for git, filesystem, shell, network, and parallel execution boundaries.
5. Create a protocol-level audit trail for what agents requested, what Ralph approved, and what happened.
6. Support session-scoped capabilities so retries, continuations, and parallel workers have bounded authority.
7. Keep phase artifacts structured and reliable without over-optimizing for a single provider.
8. Define a realistic v1 that materially improves control without requiring a full platform rewrite.

---

## Non-Goals

1. Replacing Ralph's reducer/event/effect architecture with a provider-native agent framework.
2. Turning Ralph into a general-purpose MCP platform for arbitrary third-party clients on day one.
3. Standardizing every internal Ralph event into public protocol surface in v1.
4. Solving full enterprise auth federation in the first release.
5. Eliminating XML artifacts immediately if they still provide value during transition.
6. Delegating orchestration decisions such as retries, fallback, branch routing, or recovery to agents.

---

## Product Principles

### 1. Ralph Owns The Workflow

MCP-style communication should strengthen Ralph's control plane, not weaken it. Ralph remains the host, policy engine, and deterministic orchestrator.

### 2. Capability Before Execution

Agents should operate through explicit capabilities, not through ambient authority. If Ralph cannot describe and validate an action, it should not be routable by default.

### 3. Brokered, Not Free-Access

The ideal runtime model is request -> policy evaluation -> approved execution -> structured result. Direct unmanaged access should shrink over time.

### 4. Session-Scoped Trust

Authority should be attached to a session, drain, or branch worker. Permissions granted for planning should not silently carry over to development or sibling branches.

### 5. Deterministic Outside, Flexible Inside

Ralph should remain deterministic about routing, approvals, retries, and lifecycle. Agents remain flexible about code reasoning and content generation inside the allowed boundary.

### 6. Auditability Is A Product Feature

Users should be able to answer: what did the agent ask to do, why was it allowed, and what changed as a result?

---

## Option Space

### Option A: Keep The Current Hybrid Model And Harden Around It

Ralph could continue using XML for phase artifacts, NDJSON for runtime parsing, and prompt- plus wrapper-based restrictions for environment control.

**Pros**

- lowest migration cost
- preserves all current flows
- avoids introducing a new protocol layer

**Cons**

- policy remains scattered across many enforcement points
- poor fit for least-privilege parallel agents
- no clean capability discovery model
- auditability remains fragmented

### Option B: Adopt Full MCP End-To-End Immediately

Ralph could attempt to make every runtime interaction fully MCP-native at once, replacing XML artifact handoff and provider-specific flows wherever possible.

**Pros**

- maximal protocol consistency
- strong long-term architectural story
- better alignment with ecosystem standards

**Cons**

- high migration cost
- unnecessary pressure to solve implementation detail too early
- risk of overfitting Ralph to today's MCP ecosystem gaps, especially around long-running unattended coding workflows

### Option C: Introduce An MCP-Style Ralph Control Plane (Recommended)

Ralph adopts MCP-style structured communication at the runtime boundary first, while allowing existing artifact contracts to coexist during migration.

**Pros**

- strongest product gain for the least disruption
- enables policy enforcement before action, not only after artifact generation
- preserves reducer-driven orchestration and current XML reliability where still useful
- creates a practical bridge toward broader MCP compatibility later

**Cons**

- temporary mixed model during transition
- requires protocol translation for some agent backends
- needs careful scoping so v1 stays product-focused

---

## Recommendation

Ralph should implement **Option C: an MCP-style Ralph control plane**.

In this model:

- Ralph acts as the **host and policy authority**.
- Agent runtimes connect through a **stateful client session**.
- Ralph exposes a **bounded server surface** for tools, resources, prompts, and artifact submission.
- Every tool call is **explicitly requested**, **validated**, **authorized**, and **logged**.
- XML artifact schemas may remain in place during migration, but they stop being the only structured contract Ralph relies on.

This gives Ralph the main benefits of MCP without forcing a premature all-or-nothing rewrite.

---

## Proposed Product Model

### Ralph's Role In An MCP-Style Architecture

Ralph should map cleanly onto MCP-style responsibilities:

| MCP-style role | Ralph responsibility |
|----------------|----------------------|
| Host | run lifecycle, session creation, approval policy, drain identity, branch scope |
| Client session manager | maintain per-agent connection state, capability negotiation, correlation IDs, session-scoped permissions |
| Server capability provider | expose allowed tools/resources/prompts/results to the agent |

The important distinction is that Ralph is not merely exposing tools. It is exposing a **policy-governed workflow boundary**.

### What Changes For Agents

Instead of treating the local machine as broadly available and relying on prompts to behave, the agent interacts with Ralph as a broker:

1. discover allowed capabilities for this session
2. request a tool call or resource read
3. receive approval, denial, or transformed execution result
4. submit structured progress or artifact output
5. continue until Ralph marks the session complete or interrupts it

### What Changes For Ralph

Ralph gains a first-class runtime contract for:

- least-privilege drain enforcement
- no-edit session behavior
- bounded branch worker capabilities
- approval gates for destructive or sensitive actions
- auditable parallel coordination

---

## What V1 Should Look Like

V1 should be deliberately narrow. It should solve the highest-value control-plane problems first.

### V1 Scope

#### 1. Session Handshake

Each agent invocation starts with a session handshake that declares:

- run ID
- drain identity (`planning`, `development`, `analysis`, `review`, `fix`, `commit`)
- branch or worker identity if applicable
- protocol version
- allowed capability set for this session
- session policy flags such as `no_edit`, `allow_shell`, `allow_git_read`, `allow_git_write`

#### 2. Capability Discovery

The agent can query what Ralph allows in this session. V1 should expose capabilities in a way that is stable, typed, and easy to audit.

Example v1 capability groups:

- `workspace.read`
- `workspace.write_ephemeral`
- `workspace.write_tracked`
- `git.status_read`
- `git.diff_read`
- `process.exec_bounded`
- `artifact.submit`
- `run.report_progress`

#### 3. Brokered Tool Calls

The most important v1 product change is that sensitive actions become brokered requests rather than ambient behavior.

Initial v1 tool families should include:

- **Workspace tools**: read file, list directory, search, write allowed path
- **Git read tools**: status, diff, log, show
- **Execution tools**: run bounded verification commands with timeout and policy filters
- **Artifact tools**: submit plan, issues, development result, fix result, commit message
- **Coordination tools**: report status, emit structured note, declare task complete

V1 should treat git writes and destructive filesystem actions as out of scope unless explicitly productized later.

#### 4. Policy Responses

Every request should have one of a small set of outcomes:

- approved
- denied
- approved with restriction or transformation
- requires escalation or future human approval hook

This matters because a denied action should become a structured protocol event, not a prompt-only scolding.

#### 5. Structured Audit Trail

For every session, Ralph should record:

- capability set issued
- tool calls requested
- policy outcome
- execution result metadata
- artifact submission status
- denial reasons and retry patterns

This becomes the runtime equivalent of today's event loop trace, but at the agent interaction boundary.

---

## How V1 Interacts With The Existing Orchestrator

The orchestrator flow should remain conceptually the same:

`state -> effect -> handler -> event -> state`

The change is inside the agent execution handler boundary.

### Before

- Ralph prepares prompt text
- Ralph spawns agent CLI
- agent accesses environment with broad authority
- Ralph parses NDJSON output and later validates XML artifact

### After V1

- Ralph prepares prompt text plus session capability envelope
- Ralph spawns agent session bound to the Ralph control plane
- agent requests tools and artifact submission through Ralph
- Ralph enforces policy on each request
- Ralph still parses streaming output for UX where useful
- Ralph records structured protocol events alongside existing reducer events

This preserves Ralph's core reducer architecture while upgrading the runtime boundary into something the orchestrator can govern directly.

---

## Artifact Strategy In The Transition Period

V1 should not require immediate removal of XSD artifacts.

During transition, Ralph can support a dual model:

- **artifact submission remains structured** and may still validate against existing schemas
- **runtime interaction moves to brokered MCP-style requests**

This reduces risk. Ralph can modernize the runtime boundary first, then later decide whether XML remains the best artifact format or whether some artifacts should move to another structured representation.

---

## Product Benefits

### 1. Stronger Safety

Ralph gains a cleaner path to enforce no-edit drains, bounded shell execution, and explicit git safety.

### 2. Better Parallelism

Parallel workers can receive narrow session capabilities that match their branch contract. This is much safer than broad ambient authority across multiple concurrent agents.

### 3. Better Backend Portability

A brokered control plane reduces the amount of backend-specific safety logic Ralph must encode through prompts and wrappers.

### 4. Better Observability

Operators can inspect not just final artifacts, but the actual sequence of requests, denials, approvals, and results.

### 5. Better Product Story

Ralph becomes easier to explain: the orchestrator owns workflow, permissions, and policy; agents reason about code inside a governed boundary.

---

## Risks And Mitigations

| Risk | Why it matters | Mitigation |
|------|----------------|------------|
| V1 grows too large | Product RFC turns into platform rewrite | Keep v1 focused on brokered tool access, session capabilities, and audit trail |
| Protocol complexity slows iteration | Ralph already has a working system | Preserve existing XML artifact validation during transition |
| Backend mismatch | Some agent CLIs may not map cleanly to a brokered model | Start with adapters for the drains Ralph controls most tightly |
| False sense of security | Protocol exists but ambient escape hatches remain | Treat MCP-style layer as additive at first, then reduce ambient access deliberately |
| Over-standardization | MCP ecosystem is still evolving | Keep the design MCP-style and compatible in spirit without overcommitting to every spec detail |

---

## Rollout Plan

### Phase 1: Control Plane Definition

- define session model and capability vocabulary
- define request/response envelopes and correlation semantics
- define audit fields and operator-visible logs

### Phase 2: Brokered Read-Only Sessions

- support planning, analysis, review, and commit through brokered read-focused capabilities
- keep XML artifact submission in place
- prove no-edit drains can be enforced through protocol, not only prompt text

### Phase 3: Developer Session Integration

- support development and fix drains with bounded write-capable sessions
- introduce clearer policy around tracked vs ephemeral writes
- capture richer tool-call telemetry for long-running coding sessions

### Phase 4: Parallel Worker Integration

- issue branch-scoped capability sets for parallel workers
- log worker-level request streams and reconciliation metadata
- use session scoping as part of branch isolation and cleanup correctness

### Phase 5: Artifact Contract Simplification

- evaluate where XML still provides value
- decide whether artifact submission stays XSD-based, becomes protocol-native structured payloads, or remains hybrid by drain

---

## Success Criteria

Ralph should consider this RFC successful when:

1. no-edit drains can be enforced primarily through protocol-level capability denial rather than prompt text alone
2. every brokered tool call has a structured audit record
3. session capabilities differ by drain and branch identity in ways operators can inspect
4. parallel workers can run with bounded authority that matches their contract
5. backend-specific policy code shrinks because core safety moves into Ralph's control plane
6. users can understand why an action was allowed or denied without reverse-engineering prompts and wrappers

---

## Alternatives Considered

### Provider-Native Subagents As The Product Surface

Rejected because it weakens Ralph's determinism, portability, and recovery story. Ralph should consume providers, not become subordinate to their orchestration semantics.

### Pure Wrapper-Based Enforcement

Rejected because wrappers and hooks are valuable defense-in-depth, but they are not a clean product contract for permissions, auditability, or future parallel coordination.

### Full Rewrite To Public MCP Compliance First

Rejected for v1 because the product need is better control at the runtime boundary, not immediate maximal protocol purity.

---

## Open Questions

1. Should Ralph's long-term target be strict public MCP compatibility or a Ralph-specific protocol that stays MCP-inspired?
2. Which artifact types should remain XSD-backed longest, and which are best candidates for protocol-native submission first?
3. How much approval workflow should exist in unattended mode versus deterministic auto-policy outcomes?
4. Should capability scoping be fully drain-defined, or should plans be allowed to narrow capabilities further for specific branch contracts?
5. What is the minimum viable session model needed to support continuation, XSD retry, and fallback without duplicating state across layers?

---

## References

- `docs/architecture/agents-and-prompts.md`
- `docs/architecture/event-loop-and-reducers.md`
- `docs/architecture/effect-system.md`
- `docs/architecture/streaming-and-parsers.md`
- `docs/RFC/RFC-003-streaming-architecture-hardening.md`
- `docs/RFC/RFC-008-external-parallel-worker-orchestration.md`
- `docs/agent-compatibility.md`
- Model Context Protocol specification: `https://modelcontextprotocol.io/specification/2025-03-26`
- MCP architecture docs: `https://modelcontextprotocol.io/docs/learn/architecture`
