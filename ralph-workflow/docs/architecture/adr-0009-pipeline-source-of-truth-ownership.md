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
| **Phase Progression** | `_pipeline_state_machine` (`PhaseDecision`); waiting entry via `PipelineState.set_waiting` | `run_loop._run_inner_loop`, `runner._apply_step_integration_outcome` | `run_loop`, `runner`, CLI commands | `PipelineState.current_phase`, `PipelineState.wait_reason`, status bar | `CONTINUE_PHASE` &rarr; `ADVANCE_PHASE` &rarr; `REPORT_TERMINAL_SUCCESS` / `BLOCK_FOR_RECOVERY`; waits recorded via `set_waiting` with closed `WaitReason` vocabulary (`ALL_AGENTS_UNAVAILABLE`, `EXHAUSTED_RETRY`, `COOLDOWN`); `PhaseDecision` closed vocabulary: `CONTINUE_PHASE`, `ADVANCE_PHASE`, `BLOCK_FOR_RECOVERY`, `REPORT_TERMINAL_SUCCESS`, `REPORT_TERMINAL_FAILURE`, `REPORT_INTERVENTION_REQUIRED` | Reconstituted from checkpoint; resume at current phase | `UNKNOWN` phase raises validation error; halts run loop | Unfinished phase, retained recovery, or unresolved integration blocks terminal success |
| **Integration Obligation** | `IntegrationRecordStore` (`IntegrationRecord`) | `store.transition()` (`START_INTEGRATING`, `PROMOTE_TO_INTEGRATED`, `CLEAR`) | `recover_incomplete_integration`, `inspect_integration_resolution`, `runner` | `is_integrated`, `reintegrate_pending`, `owning_worktree` | `START_INTEGRATING` &rarr; `MARK_RESOLVING_*` &rarr; `PROMOTE_TO_INTEGRATED` &rarr; `CLEAR` | Atomic `.agent/integration_record.json` reloaded; crash before/after is idempotent | Corrupt or invalid JSON yields `UNREADABLE` (typed `RecordReadOutcome.UNREADABLE` with reason `corrupt_json` / `unsupported_schema` / `backend_read_failure`); `clear_record` and `transition` refuse mutation on `UNREADABLE` and raise `RecordUnreadableError` &rarr; `REQUIRE_INTERVENTION` | Presence of un-cleared record blocks normal phase dispatch and terminal success |
| **Integration Action** | `_integration_action` (`IntegrationAction`); promotion applied exclusively by `commit_completion` — the `complete_commit()` authority at `ralph/pipeline/_commit_completion_authority.py:28` — whose result is applied by the reducer via `IntegrationRecord.with_transition` `PROMOTE_TO_INTEGRATED` | Pure function `next_integration_action()`; production callers include `run_loop`, `runner`, `auto_integrate_recovery`, and `_integration_diagnostic`; `_integration_continuation._legacy_completed_rebase` applies `complete_commit` instead | `run_loop`, `runner`, `prepare_commit_integration`, `recover_incomplete_integration` | Operator logs, dispatch decisions | Closed 8-enum transitions (`RESOLVE_ACTIVE_OPERATION`, `CONTINUE_GIT_OPERATION`, `REINTEGRATE_AGAINST_TARGET`, `LAND_TARGET`, `PUBLISH_AFTER_LANDING`, `WAIT_FOR_BOUNDED_RETRY`, `REQUIRE_INTERVENTION`, `COMPLETE_WITH_PROOF`) | Recomputed from durable record and `RebaseState` on startup | Unrecognized outcome or unreadable evidence &rarr; `REQUIRE_INTERVENTION` | Any action other than `COMPLETE_WITH_PROOF` blocks terminal success |
| **Resolution Outcome** | `RebaseState` (`last_outcome: RebaseOutcomeKind`); publication decision via `RebaseState.set_publication_decision(value: PublicationDecision)`; Git-state classification via `classify_git_operation(record) -> GitOperationKind` and `is_in_active_resolution_state(record)` (both in `ralph/pipeline/_git_operation_authority.py`); `GitOperationKind`: `RESOLVING_REBASE`, `RESOLVING_MERGE`, `AWAITING_CONTINUE`, `AWAITING_MERGE_COMMIT`, `NONE`; live-git probes combined with record flags remain observations at the caller (e.g. `_integration_continuation.py`) | Seam handlers in `auto_integrate_*.py`, `conflict_resolution/driver.py` | `next_integration_action`, `inspect_integration_resolution`, `classify_git_operation`, `is_in_active_resolution_state`, display | `last_action` (backward-compat property), `last_publication_decision`, `is_landing()` | 19 closed verbs (`REBASED`, `MERGED`, `FAST_FORWARDED`, `CONFLICT`, `INTERVENTION_REQUIRED`, etc.); publication decisions constrained to the closed `PublicationDecision` enum | Loaded from checkpoint `rebase` dictionary via `from_legacy_payload` | `RebaseOutcomeKind.UNKNOWN` and `GitOperationKind.NONE` on unreadable evidence route to `REQUIRE_INTERVENTION` | Non-terminal outcome or `INTERVENTION_REQUIRED` blocks dispatch and terminal success |
| **Resolution Failure Cause** | `RebaseState` / `IntegrationRecord` (`ResolutionFailureCause`) | `auto_integrate_budget_seam.py`, `merge.py`, `driver.py` | `_integration_diagnostic.py`, failure classification, logs | Human-readable diagnostic summaries | Typed enum: `NONE`, `AGENT_FAILURE`, `UNRESOLVED_CONFLICT`, `CONTEXT_FAILURE` | Serialized in record and checkpoint; defaults to `NONE` | Unmapped causes default to `ResolutionFailureCause.NONE` | Causes requiring intervention halt automatic resolution |
| **Landing & Remote Publication** | `_publication_state_machine` & `auto_integrate_remote_sync` | `RebaseState.set_publication_decision` (callers `push_target_after_landing`, `auto_integrate_ff`) | `is_terminal_success_eligible`, `run_loop` | `last_publication_decision`, `last_remote_sync`, `last_push` | `PUBLISH_NOW` &rarr; `REMOTE_PUSHED` / `publish_deferred_remote` / `PUBLICATION_TERMINAL` | Landing (`fast_forwarded`) preserved; publication deferred if offline | Remote timeout/unreachable &rarr; `publish_deferred_remote` (does NOT revert landing) | Pending publication requirement blocks terminal success |
| **Parallel Worker Fan-Out** | `_fan_out_authority` (`FanOutDecision`) | `RebaseState.set_fan_out_decision` (the one former raw `model_copy` site in `_fan_out_authority.py`) | `reducer`, `run_loop`, `_integrate_after_fan_out` | `WorkerStatus`, `RebaseState.last_fan_out_decision` | `JOIN_PENDING` &rarr; `JOIN_COMPLETE` / `JOIN_PARTIAL_FAILURE` / `JOIN_BLOCKED` | Rebuilt from `worker_states` in checkpoint; running workers reset to pending | Unknown worker status &rarr; `JOIN_BLOCKED` | `JOIN_PENDING` or `JOIN_BLOCKED` blocks reducer and terminal success |
| **Retry & Conflict Budget** | `RetryProgressGuard` & `apply_conflict_budget` | Guard `.record()`, `apply_conflict_budget()` | Conflict driver, run loop, backoff wait | `streak`, `consecutive_conflicts`, `retry_deadline` | Streak increments to cap &rarr; `is_exhausted = True` &rarr; `REQUIRE_INTERVENTION` | `RetryProgressGuard` snapshot persisted in `PipelineState.progress_guard` and `AgentChainState.progress_guard` atomically in checkpoint; restored via `from_dict` | Malformed or contradictory snapshots fail closed to intervention | Exhausted budget blocks automatic retry and requires operator intervention |
| **Operator Surface Display** | `OperationDisplayState` taxonomy | `RebaseState.set_operation_display_state` (10 former raw `model_copy` sites in `run_loop.py` and `runner.py`) | Rich console, status bar, event streams | 8 display labels (`INTEGRATING`, `LANDING`, `PUBLISHING`, `WAITING`, etc.) | Derived synchronously from `last_outcome` and `last_decision` | Re-projected immediately upon state load | Unknown state falls back to safe generic indicator | Pure display projection; never gates or mutates pipeline dispatch |

## Landed Refinements (2026 update)

The following implementation refinements strengthen the ownership model above; each cites its code authority.

### Terminal eligibility compares typed enum members (U2)

`ralph.pipeline._terminal_authority` compares publication and fan-out states against the imported enum members `PublicationDecision.PUBLICATION_TERMINAL` and `FanOutDecision.JOIN_COMPLETE` rather than string literals (`_terminal_authority.py:44-45`). Because these enums are `StrEnum`, legacy checkpoints that serialized plain strings still compare equal, so no migration is required. Unknown or unrecognized checkpoint strings are not equal to any terminal member and therefore continue to block terminal success (fail closed).

### Single diagnostic evidence-gate reason (U3)

The retention reason `"diagnostic evidence changed; original evidence must be restored before recovery"` has exactly one definition: `ralph.pipeline._diagnostic_evidence_gate.diagnostic_evidence_gate_reason`. The gate covers both *changed* and *unreadable* diagnostic verdicts, and both `auto_integrate_recovery.py:649` and `_remote_reconcile_recovery.py:123` import the reason from that single site. `grep` confirms exactly one definition in the tree.

### Typed record reads and BUSY/UNREADABLE ownership (U4a)

`ralph.pipeline.auto_integrate_record.read_record_outcome` returns a `RecordReadOutcome` with a closed four-disposition set — `ABSENT`, `PRESENT`, `BUSY`, `UNREADABLE`:

- `UNREADABLE` carries a typed reason (`corrupt_json`, `unsupported_schema`, `backend_read_failure`); backend read and permission failures and decoding errors all map to `backend_read_failure`.
- `BUSY` is reserved for demonstrably transient lock contention (the backend reported `EAGAIN`/`EWOULDBLOCK`). It carries NO freshly computed attempt count or deadline: `busy_attempt`/`busy_deadline` are always `None`; they come only from existing persisted retry state. An absent, invalid, or expired persisted deadline means the consumer requires intervention.
- Both `clear_record` and `IntegrationRecordStore.transition` refuse EVERY mutation — including `START_INTEGRATING` creation and `CLEAR` — on a `BUSY` record (`RecordBusyError`) or an `UNREADABLE` record (`RecordUnreadableError`); the file bytes are preserved. `ABSENT` keeps its existing semantics (idempotent, missing-ok).

### Run-loop consumption of record outcomes (U4b)

`ralph.pipeline.run_loop` consumes the typed outcomes decision-completely:

- `UNREADABLE` routes through `_handoff_retained_failure`, which persists the intervention projection exactly once (a label distinct from evidence diagnosis) and never launches a new diagnostic agent or unlinks the evidence — a corrupt or unsupported record cannot answer `diagnostic_needed`, so re-running the diagnostic would loop forever.
- `BUSY` authorizes bounded retry ONLY with a valid, unexpired persisted retry deadline; otherwise it routes to intervention.
- `_post_recovery_verdict` returns `None` when post-recovery dispatch is allowed, threading the reconciled state through `ctx.latest_state` and checkpointing it via `reconcile_stale_unresolved_state` / `_save_recovered_rebase_checkpoint`, so the caller proceeds to dispatch instead of converting a successful recovery into another block. It returns the still-blocking verdict only when recovery did not land.

### Operation-scoped rebase backup refs (U5)

Rebase backup refs are operation-scoped: `refs/rebase-backup/<wt8>-<sha8>-<sha>`, where `wt8` is the first 8 hex chars of the SHA-256 of `owning_worktree`, named by `qualified_backup_ref` (`auto_integrate_backup_refs.py:16`). Deletion during recovery is by exact ref name only (`delete_rebase_backup_refs`, `auto_integrate_recovery_terminal.py:88-131`) — never `for-each-ref` namespace enumeration. Refs from other linked worktrees sharing the same pre-feature SHA, and legacy SHA-only refs (`refs/rebase-backup/<sha8>-<sha>`), are never deleted; under-delete is the accepted failure direction and is logged. Verified by `tests/test_backup_ref_operation_scope.py` (6 cases, including the same-SHA linked-worktree collision where worktree A's cleanup preserves worktree B's ref).

**Known accepted observation (reclaim namespace)**: `_auto_integrate_reclaim.py:140` `_prune_reclaim_refs` still enumerates `refs/ralph-reclaim/<target>/` by prefix and prunes by age/count. That namespace is keyed by target branch only, so two worktrees reclaiming the same target share one prune pool — a snapshot from one worktree may be pruned by the other. This is accepted as lower-risk: reclaim snapshots are snapshot hygiene for worktree reset, not a sole recovery authority (recovery also relies on the operation-scoped backup refs above). Flagged for potential follow-up if reclaim snapshots are ever promoted to a recovery authority.

### Closed operator display label taxonomy (U8)

The operator surface exposes exactly seven integration display labels — `conflict resolution`, `evidence diagnosis`, `integration`, `landing`, `publication`, `waiting`, `intervention` — defined in `ralph.display.integration_display_label.IntegrationDisplayLabel`. Pairwise distinctness of all seven is verified by `tests/test_display_lifecycle_label_taxonomy.py`. These labels are pure display projections (Decision §1 / matrix row "Operator Surface Display") and never gate dispatch or terminal success.

### Closed-transition loader + recovery release guarantee (U9)

The loader and the three sibling release sites now flow through the closed transition table and the typed record store, closing the two real bypasses that let one lifecycle fact disagree with another:

- **Loader intervention bypass** — `_migrate_payload_evidence` no longer authors `diagnostic_intervention_required=True` via `dict(**data_raw, ...)`. The legacy global-evidence case returns a transient marker; `parse_record_payload` constructs a bare `IntegrationRecord.model_validate(...)` and applies `RAISE_DIAGNOSTIC_INTERVENTION` through the closed transition table. The intervention flag's only path to flip is the transition table.
- **Recovery release bypass** — the three sibling release sites (`auto_integrate_recovery`, `_integration_diagnostic._complete_or_intervene`, and `_remote_reconcile_recovery._apply_release_or_refuse`) now route `RELEASE_DIAGNOSTIC_EVIDENCE` through `IntegrationRecordStore.transition` (via `apply_record_transition`). Each site catches `RecordBusyError` (mapped to a typed retained-skip outcome so the bounded retry owner — `RebaseState.retry_deadline` per `_integration_action._conflict_action` — decides WAIT_FOR_BOUNDED_RETRY vs REQUIRE_INTERVENTION) and `RecordUnreadableError` (mapped to `DIAGNOSTIC_INTERVENTION_REQUIRED` with `recovery_record_retained=True` and bytes preserved on disk). The release path NEVER erases UNREADABLE bytes.

Both refinements are public-boundary: `tests/test_integration_record_closed_transition_loader.py` and `tests/test_recovery_release_uses_record_store.py` exercise the seam without AST walks.

## Recovery boundaries

Two Git reflog reads exist on the crash-recovery path. They are
distinct contracts and must not be treated as one seam:

1. **Legacy rebase completion proof.** `_integration_continuation.py`
   (`_legacy_completed_rebase`, the `git reflog show -2 <branch>`
   read) reads `commondir/logs/refs/heads/<branch>`. That row is one
   part of a multi-part proof together with branch, HEAD, the
   rebase-finish subject, and ancestry. It answers whether the legacy
   rebase sequence finished across a crash. This is crash recovery,
   not diagnostic evidence.
2. **Detached-HEAD branch reconstruction.**
   `_auto_integrate_recovery_git_state.py` `detached_branch_name`
   (the `git reflog --format=%gD -n 1` read) reads the worktree-private
   `<git-dir>/logs/HEAD`, not the commondir. It reconstructs the branch
   name from detached-HEAD residue. This is a different contract from
   the completion proof above, and it is crash recovery, not diagnostic
   evidence. `cwd=root` pins the read to the operation's own worktree.

Both seams are operation-scoped: neither read can be invalidated by an
unrelated worktree's branch advance.

## Writer inventory

Every `RebaseState` decision-field writer is a typed setter. Raw
`model_copy` writers of fan-out and operation display were migrated:
10 operation-display sites (`run_loop.py` and `runner.py`) and 1
fan-out site (`_fan_out_authority.py`).

| Decision field | Single writer (signature) | Owning authority |
|---|---|---|
| `last_publication_decision` | `RebaseState.set_publication_decision(value: PublicationDecision) -> Self` | `_publication_state_machine.next_publication_action` |
| `last_fan_out_decision` | `RebaseState.set_fan_out_decision(value: FanOutDecision) -> Self` | `_fan_out_authority.fan_out_decision` |
| `operation_display_state` | `RebaseState.set_operation_display_state(value: OperationDisplayState) -> Self` | Display projection of the authoritative lifecycle state (see `docs/sphinx/operator-states.md`) |

`set_publication_decision` is the publication writer.
`set_fan_out_decision` and `set_operation_display_state` are the
setters that replaced the raw `model_copy` writes counted above.
Ad-hoc `model_copy(update=...)` on these fields is forbidden
(Decision §1 / §3).

### Documentation review note

- **What changed:** Named the domain-matrix writer column for the three `RebaseState` setters, recorded the two crash-recovery reflog contracts, and counted the migrated display and fan-out sites separately.
- **Why here:** ADR-0009 is the ownership record for authorities, writers, readers, projections, transitions, restart, UNKNOWN, and blocking.
- **What stayed elsewhere:** Operator wording lives on the pipeline-states and recovery pages; those pages point here instead of copying the table.
- **Duplication:** Seam file locations are stated once in this section; the operator pages keep the contract distinction without a second inventory table.
- **Clearer route:** A reader can separate the shared-branch completion proof from worktree-private branch reconstruction, and can grep the three setter names.

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
