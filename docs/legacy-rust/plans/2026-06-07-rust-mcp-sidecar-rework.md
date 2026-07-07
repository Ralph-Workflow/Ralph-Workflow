# Rust MCP Performance Sidecar Rework Plan

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.

**Status:** Proposal
**Date:** 2026-06-07
**Scope:** Python-maintained `ralph-workflow/` with one concrete cross-platform Rust sidecar architecture candidate for the performance-heavy MCP runtime path

---

## Approval Bar

This plan does not pass unless independent architecture review agrees it improves all four axes:

1. architecture quality
2. fault tolerance
3. performance on targeted hot paths
4. black-box testability

---

## One Concrete Candidate

This plan is no longer a menu of possible architectures.

It proposes exactly one candidate for review.

### Candidate summary

1. **Scope**: one sidecar per provider session.
2. **Authority**: sidecar supervisor is the sole live and durable MCP runtime authority for that session.
3. **Contract mutability**: provider-visible MCP contract is immutable for that session.
4. **Crash handling**: sidecar crash invalidates that provider session; recovery happens by deterministic replacement session or fail-closed stop.
5. **Transport**: local IPC, not localhost TCP.
6. **Local IPC primitive**:
   - macOS/Linux: Unix domain socket in a private per-session directory with filesystem permission isolation
   - Windows: named pipe with process-owned ACLs bound to the launching user/session
7. **Durable store**: one embedded transactional store per provider session sidecar.
8. **Internal split**: supervisor owns status, reconciliation, and durable journaling; execution workers are replaceable subordinates.
9. **Rollback**: kill switch applies only to new sessions. Existing sidecar-owned sessions drain, reconcile, or fail closed. No in-place rollback of active sidecar-owned sessions.
10. **Streaming**: out of scope for first cutover unless a separate replay/ordering contract is approved.

This candidate should be rejected if it cannot satisfy the criteria below.

---

## Why This Candidate

The recurring review failures represented a deeper problem: earlier drafts were trying to optimize too many things at once.

This candidate intentionally gives up some ambitions:

- no live contract mutation
- no shared run-scoped sidecar
- no transparent mid-session recovery
- no provider-wide rollback of already-migrated live sessions

in exchange for:

- smaller blast radius
- simpler authority model
- safer crash semantics
- stronger black-box verifiability

---

## Primary Architectural Commitments

### 1. Session-scoped fault domain

One provider session gets one sidecar supervisor.

Required consequence:

- one bad sidecar session must not directly poison sibling sessions
- one execution worker failure must not remove status or reconciliation access for that session

### 2. Immutable provider-visible contract

For one provider session, the following are immutable:

- visible tools
- aliases
- permission mapping
- upstream namespace exposure
- caller-visible capability-derived exposure

If any of those need to change, Ralph must end or replace the provider session.

### 3. Single MCP runtime authority

For one sidecar scope:

- Python is the source of orchestration intent
- Rust sidecar is the sole live and durable MCP runtime authority

Python must not remain a second live MCP authority.

Python may keep only a minimal fail-closed recovery envelope containing:

- logical session ID
- provider session ID
- contract hash
- active revision
- sidecar incarnation ID
- issued operation IDs
- reconciliation handles

This envelope exists only for recovery decisions, not live dispatch.

### 4. Deterministic replacement-session recovery

If a sidecar crashes or wedges, only these outcomes are allowed:

1. deterministic replacement-session recovery
2. deterministic reconciliation stop
3. hard fail closed

Forbidden outcome:

- vague retry with no externally provable recovery basis

### 5. No in-place rollback of active sidecar-owned sessions

Rollback means:

- new sessions stop using the sidecar path
- existing sidecar-owned sessions are allowed to finish, reconcile, or fail closed

Rollback does **not** mean handing a live sidecar-owned session back to the old authority.

This avoids split-brain downgrade semantics.

---

## Concrete Session Identity Model

Any replacement or resumed provider session must attach using this exact tuple:

- `provider_session_id`
- `contract_hash`
- `active_revision`
- `sidecar_incarnation_id`

The meaning is:

- `provider_session_id`: logical provider conversation/session identity
- `contract_hash`: immutable caller-visible MCP contract for that session
- `active_revision`: active sidecar-owned durable runtime revision
- `sidecar_incarnation_id`: the concrete crashed-or-live sidecar process generation

Rules:

1. `provider_session_id` and `contract_hash` must match for valid replacement-session recovery.
2. `active_revision` must match the last known good active revision.
3. `sidecar_incarnation_id` must rotate on sidecar replacement.
4. A resumed or replacement session may legally keep the first three fields while changing only `sidecar_incarnation_id`.
5. The sidecar must reject attach/resume unless the tuple obeys those rules exactly.

The supervisor-owned durable journal is the authoritative source for:

- active revision
- operation status
- reconciliation handles

Execution worker loss must not remove access to that journal.

This closes the contradiction between stable logical recovery identity and rotating crashed-process identity.

---

## Concrete Contract Model

There is one canonical MCP contract definition shared across Python and Rust.

It is:

- language-neutral
- generated into both languages
- encoded using one normative byte-level canonical binary encoding

The canonical contract must define:

- tool names
- alias rules
- schemas
- permission/capability mapping
- execution owner class
- upstream namespace rules
- idempotency class for side-effecting tools

The canonical encoding must explicitly define:

- Unicode normalization
- number encoding
- default/optional/null treatment
- enum forward-compat behavior
- stable identifier encoding
- abstract path or URI normalization

Host-native raw paths are forbidden as canonical hash inputs.

Canonical request validation and error semantics must also be versioned and shared across Python and Rust for:

- unknown fields
- invalid enum values
- default application
- timeout classification
- error envelope shape

---

## Concrete Crash Recovery Model

### Revision model

The sidecar must track:

- `prepared_revision`
- `active_revision`
- `last_known_good_active_revision`

Rules:

- a prepared but not activated revision must never become live automatically after crash or restart
- restart or replacement reopens only from `last_known_good_active_revision`

### Provider replacement-session contract

For each provider in scope, the plan must define:

- the replay boundary between failed session and replacement session
- what state is replayed, summarized, or discarded
- how the replacement proves it is bound to the same logical contract
- when replacement-session recovery is allowed versus when the run must stop

Providers lacking this contract are out of scope.

---

## Concrete Operation Outcome Contract

Every provider-visible operation class must have an externally observable terminal contract after sidecar loss.

This includes:

- read-only operations
- permission-sensitive operations
- side-effecting operations
- interrupt and teardown operations

For each operation class, the plan must define:

- request identity
- terminal outcome states
- replay rules
- abort rules
- what the caller sees after sidecar crash or replacement-session recovery

No operation class may remain semantically ambiguous after crash merely because it is read-only.

### Side-effecting operations

Side-effecting and externally mutating tool classes are not in the first sidecar cutover scope for this plan.

They remain on the existing authority path until a separate follow-up proposal proves:

- safe external reconciliation
- safe downgrade and rollback
- safe replacement-session behavior after crash

For every side-effecting tool class:

- durable operation ID is required
- two-phase identity reservation is required
- external status lookup by operation ID is required
- tested downgrade behavior for `unknown` is required

Two-phase rule:

1. durably reserve provider-visible operation ID
2. expose that ID to the caller
3. only then may the side effect start

If the operation ID cannot be exposed before execution, that tool class is not eligible for cutover.

---

## Concrete Queueing and Capacity Model

The architecture must define explicit queueing and capacity boundaries for:

- provider to sidecar ingress
- sidecar control-plane work
- sidecar tool-execution work
- sidecar to Python delegation
- sidecar to upstream execution

The model must define:

- per-session limits
- global limits
- reserved control-plane capacity
- shed order
- cancel propagation rules
- timeout ownership at each boundary

Hard rule:

- status, readiness, reconciliation, revision, and interrupt traffic must be on control-plane capacity that is not consumed by ordinary tool execution

Python delegation, if any survives, must use separate worker capacity from control-plane work.

---

## Concrete Transport and Auth Model

This plan does not use localhost TCP as the approved default.

Approved candidate for Phase 0 validation:

- macOS/Linux: Unix domain socket in a private per-session directory with filesystem permission isolation
- Windows: named pipe with ACLs bound to the launching user/session

The handshake must require:

- unguessable per-session credential
- provider session ID binding
- sidecar scope binding

Before approval, the plan must prove:

- unauthorized same-host attachment fails closed
- stale or wrong-session attachment fails closed
- bind/discovery failure fails closed
- restart detection works on every supported OS

---

## Concrete Durable-Store Model

The durable store is still a hypothesis, but the candidate is now concrete:

- one embedded transactional store per sidecar session scope

This is specifically to avoid sibling-session poisoning from shared durable state.

The sidecar plan stops unless Phase 0 proves:

- write contention is acceptable
- durability overhead does not erase the hot-path win
- corruption detection and fail-closed behavior are safe
- startup/restart behavior is acceptable on supported platforms
- control-plane and status paths remain responsive while mutating calls journal

Rollback and downgrade rules must define:

- schema upgrades
- schema downgrades
- journal compatibility across rollback
- kill-switch behavior against newer on-disk state

If safe downgrade is not possible, rollback must fail closed predictably.

---

## External Verification Surfaces

Observability and fault injection are diagnostics only.

Every critical diagnostic assertion must have a matching provider-facing behavioral assertion.

### External status surface

One versioned status interface must expose at least:

- sidecar readiness
- active revision identity
- version-skew failure
- degraded upstream state
- side-effect reconciliation state

### External reconciliation surface

One versioned call-status interface keyed by durable operation ID must exist for side-effecting calls.

### Health and wedge signal

The sidecar must expose an out-of-band health or lease signal with:

- monotonic liveness updates
- explicit miss thresholds
- externally visible state transitions for healthy, degraded, and wedged

Status, readiness, reconciliation, and interrupt surfaces must retain reserved capacity under overload and stuck tool execution.

---

## Delivery Phases

### Phase 0: Approval Gate

Required deliverables:

- exact supervisor scope decision
- exact provider-facing transport, auth, and per-OS lifecycle primitives
- exact canonical schema and byte-level encoding rules
- exact durable-store candidate and per-session isolation model
- exact supervisor/worker split with supervisor-owned status and reconciliation journal
- hot-path benchmark suite with baselines
- prototype stop/go thresholds
- exact local deterministic `make verify` suite
- provider matrix proving replacement-session recovery compatibility
- exact attach/resume tuple and rejection rules
- exact downgrade contract for side-effecting tool classes
- exact external status and reconciliation contracts
- exact queueing/backpressure model across provider, sidecar, Python delegation, and upstream boundaries
- exact deterministic fake-provider and fake-upstream harness design for the local black-box suite

Rollout guardrails must also be fixed in Phase 0:

- bounded cohort size
- automatic disable thresholds for attach failures, `unknown` outcomes, and reconciliation backlog
- explicit new-session-only rollback behavior when thresholds trip

Phase 0 must also define:

- the allowlist of any surviving Python-delegated operations
- the durability class of every provider-visible operation
- the terminal outcome contract for every provider-visible operation class

Exit bar:

- if supervisor scope, transport/auth model, canonical encoding, durable-store viability, or provider recovery compatibility remain unresolved, the plan does not proceed

### Phase 1: Contract and Tool Classification

Deliverables:

- generated schema in Python and Rust
- canonical byte-level encoding rules
- tool metadata and alias rules
- execution-owner declaration per tool
- idempotency class per side-effecting tool
- Python-delegation allowlist with justification per survivor

### Phase 2: Session-Scoped Sidecar Lifecycle

Deliverables:

- chosen session/provider scope lifecycle
- durable prepared/active revision handling
- external readiness and revision visibility
- external operation-ID reservation and status lookup
- attach/resume validation using the exact tuple
- terminal externally observable outcome contract for every provider-visible operation class

### Phase 3: Hot-Path Rust Execution and Upstream Persistence

Deliverables:

- persistent upstream handling for selected hot paths
- Rust-owned registry/permission/dispatch path
- quotas and shedding rules
- reserved control-plane capacity under overload
- worker isolation proving one tool/upstream does not block control surfaces

### Phase 4: Controlled Cutover

Deliverables:

- tool-class-specific cutover eligibility
- replay/sandbox/canary strategy per side-effecting class
- provider-facing black-box canaries for non-shadowable tool classes
- kill switch and downgrade path
- tested downgrade behavior for side-effecting classes with possible `unknown`

### Phase 5: Deletion and Simplification

Deliverables:

- removal of superseded Python MCP runtime authority paths
- docs aligned to final authority model

---

## Acceptance Criteria

The plan is acceptable only when all of the following are true:

1. The chosen supervisor scope is fixed before implementation and is the narrowest scope that meets measured performance goals.
2. The provider-visible MCP contract is immutable for one provider session.
3. Caller-visible contract changes require a new provider session by default.
4. The sidecar is the sole live and durable MCP runtime authority for its session scope.
5. The canonical contract is generated once and encoded by one normative byte-level specification.
6. Hot-path provider-visible operations are not Python-delegated after cutover.
7. Every provider-visible operation class in scope for this plan has an externally observable terminal outcome contract after sidecar loss.
8. The first cutover scope excludes side-effecting and externally mutating tool classes.
9. One session or upstream fault does not directly take down unrelated sessions by default.
10. The durable-store candidate is proven viable under Phase 0 stop/go thresholds.
11. The plan includes a named deterministic local black-box suite that fits under `make verify`.
12. Every critical diagnostic assertion has a matching provider-facing behavioral assertion.
13. Non-shadowable in-scope tool classes require real provider-facing canaries before broad cutover.
14. Only providers with a deterministic replacement-session recovery contract are eligible for cutover.
15. Control-plane/status/reconciliation surfaces retain reserved capacity under overload.
16. Host-native raw paths are not used as canonical hash inputs.
17. Transport authentication and session binding are concrete approval prerequisites.
18. Storage isolation prevents one bad session from poisoning sibling sessions by default.

---

## Verification Requirements

### Local deterministic suite under `make verify`

This suite must fit the repo's strict local verification budget and include at least:

- stale-catalog rejection after caller-visible contract change
- sidecar restart gating before readiness reopen
- lost-reply reconciliation for one side-effecting tool class
- interrupt escape during degraded recovery
- attach/resume rejection on tuple mismatch
- one read-only operation crash/recovery outcome test

### Additional verification classes

- provider-facing black-box contract tests
- cross-language conformance vectors for the canonical encoding
- platform-specific lifecycle tests for macOS/Linux/Windows
- load and contention tests for the durable-store candidate
- quota/shedding tests across sibling sessions
- real canary tests for non-shadowable in-scope tool classes
- deterministic fake-provider/fake-upstream tests for crash, wedge, attach/reject, reconcile, and interrupt
- unauthorized-connect and session-binding tests

---

## Stop Conditions

The sidecar approach must stop if Phase 0 shows any of the following:

1. the durable-store candidate cannot meet hot-path latency goals
2. the chosen supervisor scope must be broadened enough to erase the fault-tolerance benefit
3. the provider matrix cannot prove deterministic replacement-session recovery
4. the local deterministic black-box suite cannot be made reliable within the repo verification budget
5. the canonical encoding cannot be made drift-proof across Python and Rust
6. the in-scope operation outcome contract cannot be made externally safe and testable
7. transport authentication or session binding cannot be made deterministic and fail closed

---

## Risks and Countermeasures

### Risk 1: Session contract drift survives

Countermeasure:

- one generated schema
- one byte-level canonical encoding
- conformance vectors before implementation approval

### Risk 2: The sidecar becomes a larger fault domain than today

Countermeasure:

- default to session/provider scope
- require benchmark proof before any broader scope

### Risk 3: Durable storage becomes the new hot path

Countermeasure:

- treat storage choice as a hypothesis
- stop if durability overhead erases the performance win

### Risk 4: Provider-visible calls become unreconcilable after failure

Countermeasure:

- require durable operation IDs
- require external reconciliation before cutover
- require externally observable terminal outcomes for every provider-visible operation class

For this plan's first rollout:

- keep side-effecting and externally mutating tool classes out of scope

### Risk 5: Tests only prove diagnostics, not real behavior

Countermeasure:

- require matching provider-facing assertions for every critical diagnostic state
- require deterministic fake-provider/fake-upstream support for the local black-box suite

### Risk 6: Dangerous tool classes cut over without real evidence

Countermeasure:

- require real canaries for non-shadowable in-scope tool classes before broad cutover
- keep side-effecting classes behind a separate follow-up proposal

### Risk 7: Provider replacement-session recovery reintroduces drift

Countermeasure:

- cut over only providers with proven deterministic replacement-session recovery contracts

### Risk 8: Transport or local attachment is spoofable or misbound

Countermeasure:

- require per-session unguessable credentials and strict session binding before any MCP traffic is accepted

---

## Final Architecture Judgment

This plan should be approved only if the first step is to validate the bounded architecture choices above, not to start porting code.

If the evidence supports:

- session/provider-scoped fault containment
- viable durable storage for the hot path
- deterministic replacement-session recovery for supported providers
- reliable black-box verification

then the Rust sidecar can improve architecture, fault tolerance, and performance.

If not, the correct outcome is to stop or narrow the project rather than forcing a sidecar into the design.
