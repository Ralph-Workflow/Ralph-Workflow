# ADR-0007: Single source of truth for the integration lifecycle

* Status: Accepted
* Date: 2026-10-14
* Deciders: Ralph Workflow Team
* Consulted: ADR-0004, ADR-0005, ADR-0006

## Context

Ralph Workflow coordinates multi-phase agent execution, automated Git integration,
and crash recovery across linked worktrees. Previously, lifecycle truth was derived
independently from multiple overlapping surfaces:

- durable integration records (`IntegrationRecord`);
- pipeline state and phase completion markers (`PipelineState`);
- checkpoint and rebase projections (`RebaseState`);
- live Git markers, refs, ancestry, index, and worktree state;
- agent exit status and submitted completion receipts;
- conflict-resolution counters, recovery controllers, and backoff state;
- remote reconciliation outcomes;
- parallel-worker and fan-out state;
- UI phase labels, alert strings, and human-readable diagnostic prose.

When these surfaces answered lifecycle questions independently (is an operation
active, who owns it, what comes next, is retry allowed, has work landed), callers
relied on local heuristics, prose parsing, or record deletion. This produced both
false progress (premature dispatch) and permanent blocking.

The immediate production reproduction occurred in linked-worktree checkouts:
`ralph/pipeline/_integration_diagnostic.py::_diagnostic_snapshot` hashed
repository-wide `git for-each-ref`. Linked worktrees share a common Git ref
namespace (`commondir`). When an unrelated agent advanced an unrelated branch in
another worktree, the repository-wide ref hash rotated. Ralph interpreted this
legitimate concurrent work as evidence corruption of the blocked operation, refused
to release the diagnostic lock, and looped through repeated diagnostic agent calls
without a terminal intervention state (`repair_last_error: diagnostic evidence changed`).
Agents were trapped in a phase displayed as "conflict resolution" even though the
underlying Git conflict had already ended.

## Decision

We establish an authoritative single-source-of-truth architecture across the
pipeline and integration lifecycle, governed by the following core decisions:

### 1. One authority per domain fact

Every lifecycle fact has exactly one authoritative owner and one transition boundary:

- **Pipeline progression**: `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  is the sole authority for what phase runs next, returning typed `PhaseDecision` values.
- **Integration obligations**: `ralph.pipeline.auto_integrate_record.IntegrationRecord`
  is the sole authority for durable integration state. All mutations must pass through
  `IntegrationRecord.with_transition(transition, ...)` as defined in
  `ralph.pipeline.auto_integrate_transition.Transition`.
- **Git operation evidence**: `ralph.pipeline.operation_evidence.OperationEvidenceCollector`
  (with atomic facts structured via `ralph.pipeline.operation_fact.OperationFact` and in-memory
  implementation in `ralph.pipeline.operation_evidence_memory`) is the sole authority permitted
  to capture and verify Git evidence for diagnostics.
- **Remote publication**: `ralph.pipeline._publication_state_machine.next_publication_action`
  is the sole authority for remote push decisions, returning `PublicationDecision`.
- **Parallel worker joins**: `ralph.pipeline._fan_out_authority` is the sole authority
  for parallel worker fan-out and join state, returning `FanOutDecision`.
- **Terminal success**: `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  is the sole authority permitted to authorize terminal workflow success.

Derived models (`RebaseState`, `StatusBarModel`, `PipelineSnapshot`) are strictly
one-way projections. Projections must never mutate or overrule their authoritative source.

#### Domain fact lifecycle authority inventory

| Domain Fact | Authoritative Owner | Authorized Writers | Readers | Projections | Key Transitions | Restart Behavior | UNKNOWN Handling | Blocking Semantics |
|---|---|---|---|---|---|---|---|---|
| **Pipeline Progression** | `_pipeline_state_machine.decide_phase_transition` | Pure function (no state writers; evaluates pure phase decisions) | `reducer.py`, `run_loop.py`, `evaluate_startup_phase_decision` | `PipelineState.phase`, `RebaseState.last_decision` | `BLOCK_FOR_RECOVERY`, `REPORT_INTERVENTION_REQUIRED`, `CONTINUE_PHASE` | Purely re-evaluated on restart from durable record and state | Defaults to `BLOCK_FOR_RECOVERY` (fails closed) | Halts forward phase dispatch; forces recovery execution |
| **Integration Obligation** | `IntegrationRecord` | Atomic disk writes via `record.with_transition(...)` in `auto_integrate.py`, `_owned_landing_reintegration.py`, `_target_reconciliation_handoff.py`, `_integration_diagnostic.py` | `run_loop.py`, `reducer.py`, `decide_phase_transition`, `auto_integrate_recovery.py` | `RebaseState.recovery_record_retained`, `RebaseState.resolving_rebase`, `RebaseState.resolving_merge` | `START_INTEGRATING`, `PROMOTE_TO_INTEGRATED`, `MARK_REINTEGRATE_PENDING`, `RAISE_DIAGNOSTIC_INTERVENTION`, `CLEAR_RECORD` | Reloaded from disk `.agent/tmp/integration_record.json` | Corrupted/malformed records rejected; unreadable triggers intervention | Durable record outranks cached decisions; blocks phase advancement until cleared |
| **Git Operation Evidence** | `OperationEvidenceCollector` (`SubprocessOperationEvidenceCollector`) | Collector queries worktree-private Git state (no global reflogs/for-each-ref) | `_integration_diagnostic.py`, `auto_integrate_recovery.py` | `OperationFact` instances | Fact collection protocol | Re-collected per operation from worktree HEAD and index | Missing/corrupt evidence fails closed (returns None / UNKNOWN) | Unreadable evidence prevents forward action; record retained for retry |
| **Remote Publication** | `_publication_state_machine.next_publication_action` | `auto_integrate_remote_sync.py` via `push_single_remote`, `push_target_after_landing` | `auto_integrate_remote_sync.py`, `reconcile_after_rejected_push` | `RebaseState.last_publication_decision`, `operation_display_state` | `PUBLISH_NOW`, `PUBLICATION_BLOCKED`, `PUBLISH_DEFERRED_FRESHNESS`, `PUBLISH_DEFERRED_REMOTE` | Re-evaluated from freshness verdict and remote reachability | Absent/unverified/degraded freshness returns `PUBLISH_DEFERRED_FRESHNESS` (fails closed) | Push mutators never invoked unless decision is `PUBLISH_NOW`; local landing preserved |
| **Parallel Fan-out / Join** | `_fan_out_authority` | `reducer.py` on `ALL_WORKERS_COMPLETE` / worker completion events | `reducer.py`, `run_loop.py` | `RebaseState.last_decision`, worker state models | `JOIN_COMPLETE`, `JOIN_BLOCKED`, `WAITING` | Preserved via checkpoint state | Incomplete join fails closed | Blocked worker join prevents terminal workflow success |
| **Terminal Success Eligibility** | `_terminal_authority.is_terminal_success_eligible` | Pure function (guards run loop terminal loop exit) | `run_loop.py` | Terminal loop continuation | Eligibility predicate | Re-evaluated from durable record and state on restart | Any active integration record or `join_blocked` returns `False` | Run loop cannot exit with success while integration is active or join is blocked |
| **Operation Display State** | `rebase_state._resolve_operation_display_state` / `auto_integrate_message` | Derived projection on `RebaseState` update | `format_auto_integrate_message`, UI live buffer, status bar | Status bar text, message strings | Maps to 8 closed states: `conflict_resolution`, `evidence_diagnosis`, `integrating`, `landing`, `publishing`, `waiting`, `intervention_required`, `terminal` | Projected freshly from state on resume | Unrecognized action falls back safely without arbitrary prose inference | Pure display projection; never feeds back into authority decisions |

### 2. Operation-scoped evidence collection

The `OperationEvidenceCollector` protocol replaces repository-global observations.
The default implementation (`SubprocessOperationEvidenceCollector`) captures only
evidence owned by the operation worktree:

- Worktree-private `HEAD` (`git rev-parse --verify HEAD`);
- The worktree root path itself (detecting worktree moves);
- Index entries strictly scoped to `record.resolving_paths` via `git ls-files --stage`;
- Worktree-private rebase/merge control state (`rebase-merge/`, `rebase-apply/`,
  `MERGE_HEAD`, `MERGE_MSG`, `REBASE_HEAD`, `ORIG_HEAD`, `AUTO_MERGE`, `CHERRY_PICK_HEAD`);
- A deterministic `ownership_fingerprint` covering non-volatile record fields.

The collector explicitly excludes:
- `git for-each-ref` (repository-global across all linked worktrees);
- Unscoped full index reads (`git ls-files --stage` without path arguments);
- Shared commondir reflogs (`logs/refs/heads/*`, `logs/HEAD`).

Unrelated branch advancements or commits in other worktrees can no longer rotate the
diagnostic hash or invalidate an active operation's evidence.

### 3. Explicit transition table for `IntegrationRecord`

`IntegrationRecord` remains a frozen model whose state transitions are governed by
the typed `Transition` definitions in `ralph.pipeline.auto_integrate_transition`:

| From State | Transition | Effect | Primary Caller |
|------------|------------|--------|----------------|
| absent | `START_INTEGRATING` | Write `phase="integrating"` | `_integrate_once_owned` |
| `integrating` | `PROMOTE_TO_INTEGRATED` | Transition `phase="integrated"`, clear resolving flags | `_promote_completed_rebase`, `_promote_completed_merge` |
| `integrating` | `MARK_RESOLVING_REBASE` | Set `resolving_rebase=True` | `set_resolving_rebase` |
| `integrating` | `MARK_PENDING_REBASE_CONTINUE` | Set `rebase_continue_pending=True` | `prepare_pending_rebase` |
| `integrating` | `MARK_RESOLVING_MERGE` | Set `resolving_merge=True`, store `resolving_paths` | `mark_pending_merge` |
| `integrating` | `MARK_PENDING_MERGE_COMMIT` | Set `merge_commit_pending=True` | `prepare_pending_merge` |
| any | `ACCEPT_REPAIR_EDITS` | Store `repair_pending_diff` and tree state | `accept_repair_edits` |
| any | `RESUME_REPAIR` | Clear `repair_pending_*` flags | `resume_source_repair` |
| any | `HOLD_DIAGNOSTIC_EVIDENCE` | Store scoped `diagnostic_evidence` and ownership | `diagnose_retained_integration` |
| any | `RELEASE_DIAGNOSTIC_EVIDENCE` | Clear diagnostic evidence and lock | `release_verified_diagnostic` |
| any | `BUMP_DIAGNOSTIC_ATTEMPT` | Increment `merge_commit_repair_attempts` | `handoff_integration_diagnostic` |
| any | `RAISE_DIAGNOSTIC_INTERVENTION` | Set `diagnostic_intervention_required=True` | `handoff_integration_diagnostic` (at cap) |
| any | `MARK_REINTEGRATE_PENDING` | Set `reintegrate_pending=True` | `reintegrate_owned_landing` |
| any | `MARK_LEGACY_FIELDS_PRESERVED` | Annotate preserved legacy migration state | `_parse_record_payload` |
| any | `CLEAR` | Unlink record file after proof | `clear_record` |

Direct untyped record mutation is prohibited. Furthermore, `clear_record` enforces a
strict safety gate: it refuses to clear records with `diagnostic_intervention_required=True`
or unresolved active operations.

### 4. Bounded progress and terminal intervention

To eliminate infinite retry loops, non-terminal diagnostic recovery is bounded by
`DIAGNOSTIC_REPAIR_ATTEMPT_CAP = 5`. When attempts exceed this cap, the diagnostic
path transitions the record via `RAISE_DIAGNOSTIC_INTERVENTION`, setting
`diagnostic_intervention_required = True`. Automatic agent rotation ceases and the
system enters an explicit intervention state requiring operator action.

### 5. Typed operator display taxonomy (`OperationDisplayState`)

Prose parsing for UI status and logs is eliminated. A typed `OperationDisplayState`
enum on `RebaseState` defines the 8 lifecycle categories:

1. `INTEGRATING`: active Git rebase or merge in progress.
2. `LANDING`: integration complete (`phase="integrated"`), local target fast-forward pending.
3. `PUBLISHING`: local landing completed; configured remote push in progress.
4. `CONFLICT_RESOLUTION`: conflict-resolution drain actively driving resolver agents.
5. `EVIDENCE_DIAGNOSIS`: diagnostic lock held to verify retained integration evidence.
6. `WAITING`: bounded retry delay or backoff cooldown active before next attempt.
7. `INTERVENTION_REQUIRED`: diagnostic repair or conflict budget exhausted; requires human operator.
8. `TERMINAL`: run finished in successful completion or non-recoverable failure.

Display formatters and status bars project directly from this typed state, ensuring
evidence diagnosis is never mislabeled as conflict resolution.

### 6. Architectural invariants

The implementation enforces the 14 core invariants:
1. *One authority per fact*: Each domain fact has a single writer and transition authority.
2. *Projections cannot overrule sources*: Derived states (`RebaseState`) never mutate or override sources.
3. *Absence is not proof*: Missing record files do not prove operation completion.
4. *Cleanliness is not landing*: A clean working tree alone does not prove target advancement.
5. *Agent success is not phase completion*: Agent rc=0 exit does not authorize phase advancement without verified artifacts/Git refs.
6. *Completed work is preserved*: Pending integration does not cause completed phase work to be replayed.
7. *Unresolved obligations block progress*: Active or intervention-required records block unsafe dispatch across all entry points.
8. *Operation-scoped evidence*: Concurrent work in other worktrees cannot invalidate operation evidence.
9. *Target movement preserves ownership*: Moving target refs shifts action to reintegration without dropping the record.
10. *UNKNOWN fails closed*: Inability to read evidence halts progress; it never authorizes cleanup.
11. *Durable progress bounds*: Retries and backoff counters survive restarts and terminate visibly.
12. *Distinct publication obligations*: Local landing and remote publication remain separate obligations.
13. *Authoritative display*: UI labels project true lifecycle states rather than currently running subroutines.
14. *Idempotent crash recovery*: Recovery safely resumes across crashes before or after any transition.

## Out of Scope (What this ADR does NOT change)

To ensure small, maintainable diffs and avoid architectural churn, the following
areas are explicitly deferred to follow-up ADRs:

- **Full reducer consolidation**: `RebaseState` retains existing compatibility fields
  (`last_action`, `last_reason`) alongside `operation_display_state` so legacy checkpoints
  deserialize without data loss.
- **Conflict-resolution session re-architecture**: The resolution ladder, strategy
  definitions, and candidate rotation in `ralph/pipeline/auto_integrate_conflict_budget.py`
  and `ralph/conflict_resolution/` remain intact under their existing contracts (ADR-0004, ADR-0005).
- **Remote-sync state machine**: `push_single_remote`, `push_target_after_landing`, and
  `_attempt_reconcile_and_push` in `ralph.pipeline.auto_integrate_remote_sync` now strictly require
  `next_publication_action` returning `PublicationDecision.PUBLISH_NOW` before invoking any remote push mutator.
  Non-fast-forward remote reconciliation loops continue to follow existing retry policies, but all mutations
  fail closed when freshness or remote reachability is absent.
- **Non-agent diagnostic repair**: Diagnostic remediation continues to invoke diagnostic
  agents up to the bounded cap; replacing agent diagnosis with purely deterministic
  syntactic repair is deferred.

## Consequences

- Linked worktrees with concurrent branch activity no longer trigger false diagnostic
  drift or lockup.
- Diagnostic recovery terminates predictably at the attempt cap, surfacing clear operator
  intervention requirements.
- Status displays, transcripts, and logs clearly distinguish evidence diagnosis from
  conflict resolution and landing.
- Legacy records and checkpoints deserialize safely with one-way migration of evidence keys.

## Verification

The single-source-of-truth architecture is verified by deterministic tests in the
test suite:
- `tests/test_operation_evidence.py`: Scoping rules, exclusions of `for-each-ref` and global index.
- `tests/test_integration_diagnostic_worktree_isolation.py`: Linked-worktree concurrency isolation.
- `tests/test_integration_diagnostic_intervention_cap.py`: Bounded repair attempts and intervention transition.
- `tests/test_pipeline_state_machine.py`: Pure progression decisions for `PhaseDecision`.
- `tests/test_publication_state_machine.py`: Distinct publication decisions for `PublicationDecision`.
- `tests/test_fan_out_authority.py`: Parallel join decisions for `FanOutDecision`.
- `tests/test_terminal_authority.py`: Single authority for terminal success eligibility.
- `tests/test_auto_integrate_record_idempotent.py`: `Transition` table validation and transition guards.
- `tests/test_integration_record_legacy_migration.py`: One-way legacy evidence migration.
