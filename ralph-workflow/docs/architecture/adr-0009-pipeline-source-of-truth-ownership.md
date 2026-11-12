# ADR-0009: Pipeline source-of-truth ownership, observation boundaries, and durable state authorities

* Status: Accepted

## Context

Ralph Workflow runs multi-phase agent execution, automated Git integration, and
crash recovery across linked worktrees. The indicators a pipeline can observe —
checkpoint fields, the durable integration record
(`.agent/integration_record.json`), live Git markers (`MERGE_HEAD`,
`REBASE_HEAD`), worktree cleanliness, agent exit statuses, and human-readable
diagnostic strings — are observations, not authorities. Each lifecycle fact
therefore has exactly one owner, every projection is derived one way from its
source, and query failures, unreadable Git states, timeouts, or missing remote
information are represented as `UNKNOWN` and fail closed.

[ADR-0007](adr-0007-integration-source-of-truth.md) and
[ADR-0008](adr-0008-integration-lifecycle-consolidation.md) scope integration
evidence, `IntegrationAction`, and `RebaseOutcomeKind`. The model below covers
source-of-truth ownership, dependency injection boundaries, and the
authoritative lifecycle domain matrix across the entire pipeline.

## Architecture

Four fundamental principles and an authoritative per-fact ownership model
govern the pipeline:

### 1. One Authority Per Domain Fact

Every pipeline and integration fact has exactly one authoritative owner, one state machine governing its valid transitions, and explicit one-way projections. Callers never infer progression independently; they request a transition from the authority and receive a typed choice:
- **Pipeline progression & phase boundaries**: `ralph.pipeline._pipeline_state_machine.decide_phase_transition` is the sole authority for whether a phase continues, advances, waits, or blocks.
- **Integration lifecycle**: the integration owner seam (`ralph.pipeline.integration_owner`: `decide_integration` / `decide_integration_verdict`, reached from the pipeline boundaries through `ralph.pipeline._integration_owner_seam.decide_seam_action`) is the sole authority for integration progression (`IntegrationAction`). `ralph.pipeline._integration_action.next_integration_action` is the typed projection onto that closed vocabulary — it fails closed to `REQUIRE_INTERVENTION` and is not an authority.
- **Terminal eligibility**: `ralph.pipeline._terminal_authority.is_terminal_success_eligible` is the sole authority governing whether the pipeline may report terminal completion.
- **Parallel fan-out joins**: `ralph.pipeline._fan_out_authority.fan_out_decision` is the sole authority for parallel worker completion and joining.

### 2. Observations Are Not Authorities

Live Git status, agent exit codes, filesystem artifacts, and network responses are observations (evidence). They enter the pipeline through explicit interfaces (Dependency Injection) and are passed to the authoritative state machine. Observers report typed facts; they do not mutate state. Query failures or unreadable evidence produce `UNKNOWN`, which fails closed and never authorizes destructive cleanup or premature forward progress.

### 3. Durable Transitions and Derived Projections

All state changes that affect recovery, retry, or phase dispatch are durable and atomic:
- State is persisted through `IntegrationRecordStore` using atomic write-and-rename semantics (`atomic_write_bytes_if_changed`).
- Projections (`OperationDisplayState`, checkpoint dumps, status snapshots, and compatibility properties) are derived one-way from the authoritative state.
- On restart, projections are reconstituted strictly from the durable authority, without multi-source timestamp merging.

### 4. Bounded Retries and Durable Budgets

Every retry counter, backoff timer, and cooldown is restart-durable and strictly bounded:
- `AgentFailureLedger` bounds zero-progress agent loops by consecutive and total attempt limits.
- `apply_conflict_budget` bounds consecutive conflict resolution attempts against identical conflict identities and calculates deadlines via an injected monotonic clock (`FakeClock` / `time.monotonic`).
- Exhaustion marks the state as requiring human intervention (`is_intervention_required = True`), halts automatic activity, and persists across restarts without replenishment.

## Source-of-Truth Domain Ownership Matrix

The following table documents the complete 8-field architectural contract for every lifecycle fact in the system:

| Fact Domain | 1. Authoritative Owner | 2. Writers | 3. Readers | 4. Derived Projections | 5. Valid Transitions | 6. Restart Behavior | 7. UNKNOWN Representation | 8. Dispatch & Terminal Blockers |
|---|---|---|---|---|---|---|---|---|
| **Phase Progression** | `_pipeline_state_machine` (`PhaseDecision`); waiting entry via `PipelineState.set_waiting` | `run_loop._run_inner_loop`, `runner._apply_step_integration_outcome` | `run_loop`, `runner`, CLI commands | `PipelineState.current_phase`, `PipelineState.wait_reason`, status bar | `CONTINUE_PHASE` &rarr; `ADVANCE_PHASE` &rarr; `REPORT_TERMINAL_SUCCESS` / `BLOCK_FOR_RECOVERY`; waits recorded via `set_waiting` with closed `WaitReason` vocabulary (`ALL_AGENTS_UNAVAILABLE`, `EXHAUSTED_RETRY`, `COOLDOWN`); `PhaseDecision` closed vocabulary: `CONTINUE_PHASE`, `ADVANCE_PHASE`, `BLOCK_FOR_RECOVERY`, `REPORT_TERMINAL_SUCCESS`, `REPORT_TERMINAL_FAILURE`, `REPORT_INTERVENTION_REQUIRED` | Reconstituted from checkpoint; resume at current phase | `UNKNOWN` phase raises validation error; halts run loop | Unfinished phase, retained recovery, or unresolved integration blocks terminal success |
| **Integration Obligation** | `IntegrationRecordStore` (`IntegrationRecord`) | `store.transition()` (`START_INTEGRATING`, `PROMOTE_TO_INTEGRATED`, `CLEAR`) | `recover_incomplete_integration`, `inspect_integration_resolution`, `runner` | `is_integrated`, `reintegrate_pending`, `owning_worktree` | `START_INTEGRATING` &rarr; `MARK_RESOLVING_*` &rarr; `PROMOTE_TO_INTEGRATED` &rarr; `CLEAR` | Atomic `.agent/integration_record.json` reloaded; crash before/after is idempotent | Corrupt or invalid JSON yields `UNREADABLE` (typed `RecordReadOutcome.UNREADABLE` with reason `corrupt_json` / `unsupported_schema` / `backend_read_failure`); `clear_record` and `transition` refuse mutation on `UNREADABLE` and raise `RecordUnreadableError` &rarr; `REQUIRE_INTERVENTION` | Presence of un-cleared record blocks normal phase dispatch and terminal success |
| **Integration Action** | `integration_owner` (owner seam: `decide_integration` / `decide_integration_verdict`) projecting onto the `_integration_action` `IntegrationAction` vocabulary; promotion applied exclusively by `commit_completion` — the `complete_commit()` authority at `ralph/pipeline/_commit_completion_authority.py:28` — whose result is applied by the reducer via `IntegrationRecord.with_transition` `PROMOTE_TO_INTEGRATED` | Owner seam `decide_integration` / `decide_integration_verdict` (durable ledger writes through `charge_integration_attempt`); the pure projection `next_integration_action()` has no production callers and is exercised by tests only; `_integration_continuation._legacy_completed_rebase` applies `complete_commit` instead | `run_loop`, `runner`, `prepare_commit_integration`, `recover_incomplete_integration` | Operator logs, dispatch decisions | Closed 8-enum transitions (`RESOLVE_ACTIVE_OPERATION`, `CONTINUE_GIT_OPERATION`, `REINTEGRATE_AGAINST_TARGET`, `LAND_TARGET`, `PUBLISH_AFTER_LANDING`, `WAIT_FOR_BOUNDED_RETRY`, `REQUIRE_INTERVENTION`, `COMPLETE_WITH_PROOF`) | Recomputed from durable record and `RebaseState` on startup | Unrecognized outcome or unreadable evidence &rarr; `REQUIRE_INTERVENTION` | Any action other than `COMPLETE_WITH_PROOF` blocks terminal success |
| **Resolution Outcome** | `RebaseState` (`last_outcome: RebaseOutcomeKind`); publication choice via `RebaseState.set_publication_decision(value: PublicationDecision)`; Git-state classification via `classify_git_operation(record) -> GitOperationKind` and `is_in_active_resolution_state(record)` (both in `ralph/pipeline/_git_operation_authority.py`); `GitOperationKind`: `RESOLVING_REBASE`, `RESOLVING_MERGE`, `AWAITING_CONTINUE`, `AWAITING_MERGE_COMMIT`, `NONE`; live-git probes combined with record flags remain observations at the caller (e.g. `_integration_continuation.py`) | Seam handlers in `auto_integrate_*.py`, `conflict_resolution/driver.py` | `decide_seam_action` (owner seam), `inspect_integration_resolution`, `classify_git_operation`, `is_in_active_resolution_state`, display | `last_action` (backward-compat property), `last_publication_decision`, `is_landing()` | 19 closed verbs (`REBASED`, `MERGED`, `FAST_FORWARDED`, `CONFLICT`, `INTERVENTION_REQUIRED`, etc.); publication decisions constrained to the closed `PublicationDecision` enum | Loaded from checkpoint `rebase` dictionary via `from_legacy_payload` | `RebaseOutcomeKind.UNKNOWN` and `GitOperationKind.NONE` on unreadable evidence route to `REQUIRE_INTERVENTION` | Non-terminal outcome or `INTERVENTION_REQUIRED` blocks dispatch and terminal success |
| **Resolution Failure Cause** | `RebaseState` / `IntegrationRecord` (`ResolutionFailureCause`) | `auto_integrate_budget_seam.py`, `merge.py`, `driver.py` | `_integration_diagnostic.py`, failure classification, logs | Human-readable diagnostic summaries | Typed enum: `NONE`, `AGENT_FAILURE`, `UNRESOLVED_CONFLICT`, `CONTEXT_FAILURE` | Serialized in record and checkpoint; defaults to `NONE` | Unmapped causes default to `ResolutionFailureCause.NONE` | Causes requiring intervention halt automatic resolution |
| **Landing & Remote Publication** | `_publication_state_machine` & `auto_integrate_remote_sync` | `RebaseState.set_publication_decision` (callers `push_target_after_landing`, `auto_integrate_ff`) | `is_terminal_success_eligible`, `run_loop` | `last_publication_decision`, `last_remote_sync`, `last_push` | `PUBLISH_NOW` &rarr; `REMOTE_PUSHED` / `publish_deferred_remote` / `PUBLICATION_TERMINAL` | Landing (`fast_forwarded`) preserved; publication deferred if offline | Remote timeout/unreachable &rarr; `publish_deferred_remote` (does NOT revert landing) | Pending publication requirement blocks terminal success |
| **Parallel Worker Fan-Out** | `_fan_out_authority` (`FanOutDecision`) | `RebaseState.set_fan_out_decision` (1 fan-out site in `_fan_out_authority.py`) | `reducer`, `run_loop`, `_integrate_after_fan_out` | `WorkerStatus`, `RebaseState.last_fan_out_decision` | `JOIN_PENDING` &rarr; `JOIN_COMPLETE` / `JOIN_PARTIAL_FAILURE` / `JOIN_BLOCKED` | Rebuilt from `worker_states` in checkpoint; running workers reset to pending | Unknown worker status &rarr; `JOIN_BLOCKED` | `JOIN_PENDING` or `JOIN_BLOCKED` blocks reducer and terminal success |
| **Retry & Conflict Budget** | `AgentFailureLedger` & `apply_conflict_budget` | Ledger `.record_attempt_fact()`, `apply_conflict_budget()` | Conflict driver, run loop, backoff wait | `consecutive_unproductive`, `total_unproductive`, `consecutive_conflicts`, `retry_deadline` | Consecutive or total reaches limit &rarr; demotion/exhaustion &rarr; `REQUIRE_INTERVENTION` | `AgentFailureLedger` snapshot persisted in `PipelineState.ledger_snapshot` atomically in checkpoint; restored via `from_snapshot` | Malformed or contradictory snapshots fail closed to intervention | Exhausted budget blocks automatic retry and requires operator intervention |
| **Operator Surface Display** | `OperationDisplayState` taxonomy | `RebaseState.set_operation_display_state` (10 operation-display sites in `run_loop.py` and `runner.py`) | Rich console, status bar, event streams | 8 display labels (`INTEGRATING`, `LANDING`, `PUBLISHING`, `WAITING`, etc.) | Derived synchronously from `last_outcome` and `last_decision` | Re-projected immediately upon state load | Unknown state falls back to safe generic indicator | Pure display projection; never gates or mutates pipeline dispatch |

## Refinements

The following implementation refinements strengthen the ownership model above; each cites its code authority.

### Terminal eligibility compares typed enum members (U2)

`ralph.pipeline._terminal_authority` compares publication and fan-out states against the imported enum members `PublicationDecision.PUBLICATION_TERMINAL` and `FanOutDecision.JOIN_COMPLETE` rather than string literals (`_terminal_authority.py:44-45`). Because these enums are `StrEnum`, legacy checkpoints that serialized plain strings still compare equal. Unknown or unrecognized checkpoint strings are not equal to any terminal member and therefore continue to block terminal success (fail closed).

### Single diagnostic evidence-gate reason (U3)

The retention reason `"diagnostic evidence changed; original evidence must be restored before recovery"` has exactly one definition: `ralph.pipeline._diagnostic_evidence_gate.diagnostic_evidence_gate_reason`. The gate covers both *changed* and *unreadable* diagnostic verdicts, and both `auto_integrate_recovery.py:649` and `_remote_reconcile_recovery.py:123` import the reason from that single site. `grep` confirms exactly one definition in the tree.

### Typed record reads and BUSY/UNREADABLE ownership (U4a)

`ralph.pipeline.auto_integrate_record.read_record_outcome` returns a `RecordReadOutcome` with a closed four-disposition set — `ABSENT`, `PRESENT`, `BUSY`, `UNREADABLE`:

- `UNREADABLE` carries a typed reason (`corrupt_json`, `unsupported_schema`, `backend_read_failure`); backend read and permission failures and decoding errors all map to `backend_read_failure`.
- `BUSY` is reserved for demonstrably transient lock contention (the backend reported `EAGAIN`/`EWOULDBLOCK`). It carries NO freshly computed attempt count or deadline: `busy_attempt`/`busy_deadline` are always `None`; they come only from existing persisted retry state. An absent, invalid, or expired persisted deadline means the consumer requires intervention.
- Both `clear_record` and `IntegrationRecordStore.transition` refuse EVERY mutation — including `START_INTEGRATING` creation and `CLEAR` — on a `BUSY` record (`RecordBusyError`) or an `UNREADABLE` record (`RecordUnreadableError`); the file bytes are preserved. `ABSENT` is idempotent and missing-ok.

### Run-loop consumption of record outcomes (U4b)

`ralph.pipeline.run_loop` consumes the typed outcomes choice-completely:

- `UNREADABLE` routes through `_handoff_retained_failure`, which persists the intervention projection exactly once (a label distinct from evidence diagnosis) and never launches a new diagnostic agent or unlinks the evidence — a corrupt or unsupported record cannot answer `diagnostic_needed`, so re-running the diagnostic would loop forever.
- `BUSY` authorizes bounded retry ONLY with a valid, unexpired persisted retry deadline; otherwise it routes to intervention.
- `_post_recovery_verdict` returns `None` when post-recovery dispatch is allowed, threading the reconciled state through `ctx.latest_state` and checkpointing it via `reconcile_stale_unresolved_state` / `_save_recovered_rebase_checkpoint`, so the caller proceeds to dispatch instead of converting a successful recovery into another block. It returns the still-blocking verdict only when recovery did not land.

### Operation-scoped rebase backup refs (U5)

Rebase backup refs are operation-scoped: `refs/rebase-backup/<wt8>-<sha8>-<sha>`, where `wt8` is the first 8 hex chars of the SHA-256 of `owning_worktree`, named by `qualified_backup_ref` (`auto_integrate_backup_refs.py:16`). Deletion during recovery is by exact ref name only (`delete_rebase_backup_refs`, `auto_integrate_recovery_terminal.py:88-131`) — never `for-each-ref` namespace enumeration. Refs from other linked worktrees sharing the same pre-feature SHA, and legacy SHA-only refs (`refs/rebase-backup/<sha8>-<sha>`), are never deleted; under-delete is the accepted failure direction and is logged. Verified by `tests/test_backup_ref_operation_scope.py` (6 cases, including the same-SHA linked-worktree collision where worktree A's cleanup preserves worktree B's ref).

**Reclaim namespace pruning**: `_auto_integrate_reclaim.py:140` `_prune_reclaim_refs` enumerates `refs/ralph-reclaim/<target>/` by prefix and prunes by age/count. That namespace is keyed by target branch only, so two worktrees reclaiming the same target share one prune pool — a snapshot from one worktree may be pruned by the other. Reclaim snapshots are snapshot hygiene for worktree reset, not a recovery authority; recovery relies on the operation-scoped backup refs above.

### Closed operator display label taxonomy (U8)

The operator surface exposes exactly seven integration display labels — `conflict resolution`, `evidence diagnosis`, `integration`, `landing`, `publication`, `waiting`, `intervention` — defined in `ralph.display.integration_display_label.IntegrationDisplayLabel`. Pairwise distinctness of all seven is verified by `tests/test_display_lifecycle_label_taxonomy.py`. These labels are pure display projections (Architecture §1 / matrix row "Operator Surface Display") and never gate dispatch or terminal success.

### Closed-transition loader + recovery release guarantee (U9)

The loader and the three sibling release sites flow through the closed transition table and the typed record store:

- **Loader intervention path** — `_migrate_payload_evidence` returns a transient marker for the legacy global-evidence case; `parse_record_payload` constructs a bare `IntegrationRecord.model_validate(...)` and applies `RAISE_DIAGNOSTIC_INTERVENTION` through the closed transition table. The intervention flag's only path to flip is the transition table.
- **Recovery release path** — the three sibling release sites (`auto_integrate_recovery`, `_integration_diagnostic._complete_or_intervene`, and `_remote_reconcile_recovery._apply_release_or_refuse`) route `RELEASE_DIAGNOSTIC_EVIDENCE` through `IntegrationRecordStore.transition` (via `apply_record_transition`). Each site catches `RecordBusyError` (mapped to a typed retained-skip outcome so the bounded retry owner — `RebaseState.retry_deadline` per `_integration_action._conflict_action` — decides WAIT_FOR_BOUNDED_RETRY vs REQUIRE_INTERVENTION) and `RecordUnreadableError` (mapped to `DIAGNOSTIC_INTERVENTION_REQUIRED` with `recovery_record_retained=True` and bytes preserved on disk). The release path NEVER erases UNREADABLE bytes.

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
   (the `git reflog --format=%H%x00%gs -n 50` read at line 214 of
   `detached_branch_name`) reads the worktree-private
   `<git-dir>/logs/HEAD`, not the commondir. The format string emits
   the commit hash (`%H`) followed by a NUL byte and the reflog
   subject (`%gs`); each entry is `<sha>\x00<subject>`. The bounded
   `-n 50` window keeps the read finite. The seam reconstructs the
   branch name from detached-HEAD residue, scans the entries
   newest-first, and returns the **first** checkout/switch entry
   whose destination is an existing local branch whose tip EQUALS
   detached HEAD exactly (no `is_ancestor` fall-through, no fall-
   through to older reflog entries on failed observation — fail-
   closed). This is a different contract from the completion proof
   above, and it is crash recovery, not diagnostic evidence.
   `cwd=root` pins the read to the operation's own worktree.

Both seams are operation-scoped: neither read can be invalidated by an
unrelated worktree's branch advance.

## Writer inventory

Every `RebaseState` choice-field writer is a typed setter. The
operation-display field is written through `set_operation_display_state` at 10
sites in `run_loop.py` and `runner.py`; the fan-out field is written through
`set_fan_out_decision` at 1 site in `_fan_out_authority.py`. The
conflict-strategy / deadline / intervention fields are owned by typed setters at
`reducer.py:610-617`,
`auto_integrate_conflict_budget.py:257-312`,
`run_loop.py:2339-2347`, `runner.py:1002-1011`, and
`auto_integrate_resolution_state.py:77`. The reducer branches on
the typed `FailureCategory.INTEGRATION` field of `PhaseFailureEvent`
(consumed via an optional `failure_category` parameter on
`_enter_failed_recovery` and `_handle_agent_failure`); the prose
prefix `"integration conflict requires resolution:"` is not consulted by the
reducer.

| Choice field | Single writer (signature) | Owning authority |
|---|---|---|
| `last_publication_decision` | `RebaseState.set_publication_decision(value: PublicationDecision) -> Self` | `_publication_state_machine.next_publication_action` |
| `last_fan_out_decision` | `RebaseState.set_fan_out_decision(value: FanOutDecision) -> Self` | `_fan_out_authority.fan_out_decision` |
| `operation_display_state` | `RebaseState.set_operation_display_state(value: OperationDisplayState) -> Self` | Display projection of the authoritative lifecycle state (see `docs/sphinx/operator-states.md`) |
| `retry_deadline` | `RebaseState.set_retry_deadline(value: float \| None) -> Self` | `apply_conflict_budget` and `_wait_for_integration_retry` via the typed `FailureCategory.INTEGRATION` seam |
| `consecutive_conflicts` | `RebaseState.record_consecutive_conflicts(value: int) -> Self` (clamped to `MAX_TRACKED_CONFLICT_ATTEMPTS` inside the setter; repository bookkeeping only -- resolver eligibility is owned by `AgentFailureLedger`) | `apply_conflict_budget` CONFLICT branch only |
| `conflict_strategy_index`, `conflict_strategies_tried`, `resolution_exhausted`, `resolution_exhaustion_reason` (bounded advancement) | `RebaseState.advance_conflict_strategy(reason: str) -> Self` | `_enter_failed_recovery` / `_handle_agent_failure` when `failure_category is FailureCategory.INTEGRATION` |
| `conflict_strategy_index`, `conflict_strategies_tried`, `resolution_exhausted`, `resolution_exhaustion_reason` (exact one-way projection) | `RebaseState.project_conflict_strategy_from(source: RebaseState) -> Self` | `_merge_strategy_onto_outcome` (the projection applied at `runner.py:1002-1011`) |
| `conflict_strategy_index`, `conflict_strategies_tried`, `resolution_exhausted`, `resolution_exhaustion_reason` (reset to defaults) | `RebaseState.clear_conflict_strategy() -> Self` | `reconcile_stale_unresolved_state` when the strategy block is stale or never started |
| `resolution_exhausted`, `resolution_exhaustion_reason` (exhaustion-only reset, preserves strategy index and tried list) | `RebaseState.clear_resolution_exhausted() -> Self` | `reconcile_stale_unresolved_state` when the exhausted flag must be released without losing operator history |
| `last_outcome`, `last_reason` (persistent `INTERVENTION_REQUIRED` / `DIAGNOSTIC_INTERVENTION_REQUIRED` transition) | `RebaseState.set_intervention_required(reason: str, *, diagnostic: bool = False) -> Self` | `_wait_for_integration_retry` on the exhaustion path |
| `last_planning_obstruction` | assigned by `auto_integrate_planning.record_conflict` to one of the closed `PlanningObstructionKind` members; the single producer is `identify_planning_obstruction` in `ralph/pipeline/_planning_obstruction.py` | `_route_planning_commit` reads only the typed kind from `state.rebase.last_planning_obstruction` |

`set_publication_decision` is the publication writer.
`set_fan_out_decision` and `set_operation_display_state` own the fan-out and
operation-display fields. `set_retry_deadline`, `record_consecutive_conflicts`,
`advance_conflict_strategy`, `project_conflict_strategy_from`,
`clear_conflict_strategy`, `clear_resolution_exhausted`, and
`set_intervention_required` own the conflict-strategy and intervention fields
in `reducer.py`, `auto_integrate_conflict_budget.py`, `run_loop.py`,
`runner.py`, and `auto_integrate_resolution_state.py`.

Ad-hoc `model_copy(update=...)` on any of the 11 fields named in
this table is forbidden (Architecture §1 / §3). The audit that enforces
this is `tests/test_no_superseded_paths_typed_setter_contract.py`
(method-body-scoped allowlist of the 7 setter bodies plus
variable-backed `update=<Name|Call|Subscript>` detection); the
broader raw-writer audit on the other four fields lives in
`tests/test_rebase_state_setters_owner.py`. Both audits are part of
`make verify`.

### Typed `FailureCategory` seam on the reducer

The reducer consumes the typed `failure_category` field of
`PhaseFailureEvent` through the optional keyword-only
`failure_category: FailureCategory | None = None` parameter on
`_enter_failed_recovery` and `_handle_agent_failure`. The producer
`runner._integration_conflict_failure` passes
`failure_category=FailureCategory.INTEGRATION`. No prose comparison exists
on the failure reason: the prefix
`"integration conflict requires resolution:"` is not consulted at
`reducer.py:603` or `:626`, and no string fallback is permitted (when
`failure_category is None` the reducer does NOT advance the
conflict-strategy ladder).

### Planning-obstruction typed seam

The run loop's `_route_planning_commit` reads the obstruction flag from the
typed `PlanningObstructionKind` at `state.rebase.last_planning_obstruction`.
The planning-obstruction substrings are represented by a closed
`PlanningObstructionKind` StrEnum
(`NONE`, `LOCAL_OVERWRITE`, `UNTRACKED_OVERWRITE`,
`STAGED_BLOCKS_INTEGRATION`) in
`ralph/pipeline/_planning_obstruction.py`. The single producer
`identify_planning_obstruction(reason)` translates the free-form
reason into a kind; the run loop reads only the typed kind from
`state.rebase.last_planning_obstruction`. No `any(text in reason ...)`
substring scan runs in the run loop (`run_loop.py:1797`); the helper is the
only allowed prose-substring scan on the planning-seam reason.

### Attempt-fault trust boundary

`ralph/pipeline/conflict_resolution/attempt_fault.py::classify_attempt_fault`
authorizes `AttemptFault.AGENT_HEALTHY` only on an exact match:
`text.lower() == "healthy"` (line 154). Arbitrary agent output
containing `"healthy"` as a substring classifies to
`AttemptFault.AGENT_EXITED`, not `AGENT_HEALTHY`. The two
closed-vocabulary scans at
`attempt_fault.py:115` (the named token
`SUPERVISION_INFRASTRUCTURE_FAILURE`) and
`attempt_fault.py:154` (`any(k in lowered for k in (...))` against
`ResolutionTerminationReason` enum member identifiers) consult a closed
vocabulary, not free-form prose.

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
