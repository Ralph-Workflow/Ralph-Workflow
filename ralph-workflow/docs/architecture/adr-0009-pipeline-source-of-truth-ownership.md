# ADR-0009: Pipeline source-of-truth ownership, observation boundaries, and durable state authorities

* Status: Accepted
* Date: 2026-10-15
* Deciders: Ralph Workflow Team
* Consulted: ADR-0006, ADR-0007, ADR-0008

## Context

Previous iterations of Ralph Workflow accumulated multiple competing representations of truth across the pipeline:
1. **Drifting authorities**: Pipeline phase progression, startup recovery, commit handling, parallel worker joins, and terminal success independently inspected diverse indicators — checkpoint fields, presence/absence of `.agent/integration_record.json`, live Git markers (`MERGE_HEAD`, `REBASE_HEAD`), worktree cleanliness, or agent exit statuses.
2. **Prose-driven control flow**: Decision paths parsed human-readable diagnostic strings (`last_reason`, `phase == "integrating"`), conflating user-facing presentation with internal lifecycle state.
3. **Collapsing UNKNOWN into negative**: Query failures, unreadable Git states, timeout, or missing remote information were frequently collapsed into false negatives that authorized inappropriate dispatch, cleanup, or abandonment.
4. **Transient retry counters**: Retry budgets and backoff cooldowns resided in volatile process memory or were reset on restart, allowing infinite spiral retry loops across process invocations.
5. **Observation vs Authority conflation**: Live Git commands, filesystem queries, and network responses acted as ad-hoc decision authorities rather than input facts to an authoritative state machine.

[ADR-0007](adr-0007-integration-source-of-truth.md) and [ADR-0008](adr-0008-integration-lifecycle-consolidation.md) introduced initial scoping for integration evidence, `IntegrationAction`, and `RebaseOutcomeKind`. This ADR establishes the complete, unified architectural model of source-of-truth ownership, dependency injection boundaries, and the authoritative lifecycle domain matrix across the entire pipeline.

## Decision

We establish four fundamental principles and an authoritative per-fact ownership model:

### 1. One Authority Per Domain Fact

Every pipeline and integration fact has exactly one authoritative owner, one state machine governing its valid transitions, and explicit one-way projections. Callers never infer progression independently; they request a transition from the authority and receive a typed decision:
- **Pipeline progression & phase boundaries**: `ralph.pipeline._pipeline_state_machine.decide_phase_transition` is the sole authority for whether a phase continues, advances, waits, or blocks.
- **Integration lifecycle**: `ralph.pipeline._integration_action.next_integration_action` is the sole authority for integration progression (`IntegrationAction`).
- **Terminal eligibility**: `ralph.pipeline._terminal_authority.is_terminal_success_eligible` is the sole authority governing whether the pipeline may report terminal completion.
- **Parallel fan-out joins**: `ralph.pipeline._fan_out_authority.fan_out_decision` is the sole authority for parallel worker completion and joining.

### 2. Observations Are Not Authorities

Live Git status, agent exit codes, filesystem artifacts, and network responses are observations (evidence). They enter the pipeline through explicit interfaces (Dependency Injection) and are passed to the authoritative state machine. Observers report typed facts; they do not mutate state. Query failures or unreadable evidence produce `UNKNOWN`, which fails closed and never authorizes destructive cleanup or premature forward progress.

### 3. Durable Transitions and Derived Projections

All state changes that affect recovery, retry, or phase dispatch are durable and atomic:
- State is persisted through `IntegrationRecordStore` using atomic write-and-rename semantics (`atomic_write_bytes_if_changed`).
- Projections (`OperationDisplayState`, checkpoint dumps, status snapshots, and compatibility properties) are derived one-way from the authoritative state.
- On restart, projections are reconstituted strictly from the durable authority; multi-source timestamp merging is eliminated.

### 4. Bounded Retries and Durable Budgets

Every retry counter, backoff timer, and cooldown is restart-durable and strictly bounded:
- `RetryProgressGuard` bounds zero-progress agent loops by identical-failure signature.
- `apply_conflict_budget` bounds consecutive conflict resolution attempts against identical conflict identities and calculates deadlines via an injected monotonic clock (`FakeClock` / `time.monotonic`).
- Exhaustion marks the state as requiring human intervention (`is_intervention_required = True`), halts automatic activity, and persists across restarts without replenishment.

## Source-of-Truth Domain Ownership Matrix

The following table documents the complete 8-field architectural contract for every lifecycle fact in the system:

| Fact Domain | 1. Authoritative Owner | 2. Writers | 3. Readers | 4. Derived Projections | 5. Valid Transitions | 6. Restart Behavior | 7. UNKNOWN Representation | 8. Dispatch & Terminal Blockers |
|---|---|---|---|---|---|---|---|
| **Phase Progression** | `_pipeline_state_machine` (`PhaseDecision`) | `run_loop._run_inner_loop`, `runner._apply_step_integration_outcome` | `run_loop`, `runner`, CLI commands | `PipelineState.current_phase`, status bar | `CONTINUE_PHASE` &rarr; `ADVANCE_PHASE` &rarr; `REPORT_TERMINAL_SUCCESS` / `BLOCK_FOR_RECOVERY` | Reconstituted from checkpoint; resume at current phase | `UNKNOWN` phase raises validation error; halts run loop | Unfinished phase, retained recovery, or unresolved integration blocks terminal success |
| **Integration Obligation** | `IntegrationRecordStore` (`IntegrationRecord`) | `store.transition()` (`START_INTEGRATING`, `PROMOTE_TO_INTEGRATED`, `CLEAR`) | `recover_incomplete_integration`, `inspect_integration_resolution`, `runner` | `is_integrated`, `reintegrate_pending`, `owning_worktree` | `START_INTEGRATING` &rarr; `MARK_RESOLVING_*` &rarr; `PROMOTE_TO_INTEGRATED` &rarr; `CLEAR` | Atomic `.agent/integration_record.json` reloaded; crash before/after is idempotent | Corrupt or invalid JSON fails closed (returns `None` &rarr; `REQUIRE_INTERVENTION`) | Presence of un-cleared record blocks normal phase dispatch and terminal success |
| **Integration Action** | `_integration_action` (`IntegrationAction`) | Pure function `next_integration_action()` | `run_loop`, `runner`, `prepare_commit_integration`, `recover_incomplete_integration` | Operator logs, dispatch decisions | Closed 8-enum transitions (`RESOLVE_ACTIVE`, `CONTINUE_GIT`, `REINTEGRATE`, `LAND`, `PUBLISH`, `WAIT`, `INTERVENE`, `COMPLETE`) | Recomputed from durable record and `RebaseState` on startup | Unrecognized outcome or unreadable evidence &rarr; `REQUIRE_INTERVENTION` | Any action other than `COMPLETE_WITH_PROOF` blocks terminal success |
| **Resolution Outcome** | `RebaseState` (`last_outcome: RebaseOutcomeKind`) | Seam handlers in `auto_integrate_*.py`, `conflict_resolution/driver.py` | `next_integration_action`, `inspect_integration_resolution`, display | `last_action` (backward-compat property), `is_landing()` | 19 closed verbs (`REBASED`, `MERGED`, `FAST_FORWARDED`, `CONFLICT`, `INTERVENTION_REQUIRED`, etc.) | Loaded from checkpoint `rebase` dictionary via `from_legacy_payload` | `RebaseOutcomeKind.UNKNOWN` routes to `REQUIRE_INTERVENTION` | Non-terminal outcome or `INTERVENTION_REQUIRED` blocks dispatch and terminal success |
| **Resolution Failure Cause** | `RebaseState` / `IntegrationRecord` (`ResolutionFailureCause`) | `auto_integrate_budget_seam.py`, `merge.py`, `driver.py` | `_integration_diagnostic.py`, failure classification, logs | Human-readable diagnostic summaries | Typed enum: `NONE`, `AGENT_FAILURE`, `UNRESOLVED_CONFLICT`, `CONTEXT_FAILURE` | Serialized in record and checkpoint; defaults to `NONE` | Unmapped causes default to `ResolutionFailureCause.NONE` | Causes requiring intervention halt automatic resolution |
| **Landing & Remote Publication** | `_publication_state_machine` & `auto_integrate_remote_sync` | `push_target_after_landing`, `auto_integrate_ff` | `is_terminal_success_eligible`, `run_loop` | `last_publication_decision`, `last_remote_sync`, `last_push` | `PUBLISH_NOW` &rarr; `REMOTE_PUSHED` / `publish_deferred_remote` / `publish_terminal` | Landing (`fast_forwarded`) preserved; publication deferred if offline | Remote timeout/unreachable &rarr; `publish_deferred_remote` (does NOT revert landing) | Pending publication requirement blocks terminal success |
| **Parallel Worker Fan-Out** | `_fan_out_authority` (`FanOutDecision`) | `handle_worker_*` event handlers | `reducer`, `run_loop`, `_integrate_after_fan_out` | `WorkerStatus`, `RebaseState.last_fan_out_decision` | `JOIN_PENDING` &rarr; `JOIN_COMPLETE` / `JOIN_PARTIAL_FAILURE` / `JOIN_BLOCKED` | Rebuilt from `worker_states` in checkpoint; running workers reset to pending | Unknown worker status &rarr; `JOIN_BLOCKED` | `JOIN_PENDING` or `JOIN_BLOCKED` blocks reducer and terminal success |
| **Retry & Conflict Budget** | `RetryProgressGuard` & `apply_conflict_budget` | Guard `.record()`, `apply_conflict_budget()` | Conflict driver, run loop, backoff wait | `streak`, `consecutive_conflicts`, `retry_deadline` | Streak increments to cap &rarr; `is_exhausted = True` &rarr; `REQUIRE_INTERVENTION` | `RetryProgressGuard` snapshot persisted in `PipelineState.progress_guard` and `AgentChainState.progress_guard` atomically in checkpoint; restored via `from_dict` | Malformed or contradictory snapshots fail closed to intervention | Exhausted budget blocks automatic retry and requires operator intervention |
| **Operator Surface Display** | `OperationDisplayState` taxonomy | Computed projection from `RebaseState.operation_display_state` | Rich console, status bar, event streams | 8 display labels (`INTEGRATING`, `LANDING`, `PUBLISHING`, `WAITING`, etc.) | Derived synchronously from `last_outcome` and `last_decision` | Re-projected immediately upon state load | Unknown state falls back to safe generic indicator | Pure display projection; never gates or mutates pipeline dispatch |

## Dependency Injection Boundaries

To ensure complete black-box testability without live Git or filesystem side-effects in unit suites:
1. **Filesystem and Persistence**: `IntegrationRecordStore` and `Checkpoint` accept an injected `FileBackend` (defaulting to atomic filesystem I/O, substituted with `_InMemoryBackend` in tests).
2. **Git Observations**: Operations accept injectable query callables (`porcelain`, `rebase_active`, `merge_status`) and runner options, reporting typed structures (`GitRunResult`, `MergeState`) rather than raw returncodes.
3. **Monotonic Clock**: Durations, backoff deadlines, and retry expirations accept an injected monotonic callable (`clock: Callable[[], float]`), allowing simulated time advancement (`FakeClock`) without real-time sleeps.
4. **Remote Operations**: Remote synchronization accepts an injected `RemoteClient` (`push_branch`, `refresh_target`, `observe_remote_sha`) returning structured `PushResult` objects, isolating network state.
5. **Agent Invocation**: Conflict resolution and phase dispatch interact with agents via injected invoker interfaces (`AgentInvokerLike`), preventing out-of-process subprocess spawns during testing.

## References

* [ADR-0006: Owned landing recovery](adr-0006-owned-landing-recovery.md)
* [ADR-0007: Integration source of truth](adr-0007-integration-source-of-truth.md)
* [ADR-0008: Integration lifecycle consolidation](adr-0008-integration-lifecycle-consolidation.md)
* Canonical verification policy: `docs/ralph-workflow-policy/verification-policy.md`
