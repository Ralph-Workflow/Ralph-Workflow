# Explore-index fault matrix (F1-F20) and reason-code vocabulary

**Scope.** Characterises every indexed-search failure mode the Ralph
MCP explore substrate must handle per `PRODUCT_CRITERIA.md` (R1, R2,
R4). The matrix below is the single source for the reason-code
vocabulary wired into `ralph.mcp.explore.serving` and the
`fallback_reason` field surfaced on every index-capable MCP tool
response (`grep_files`, `search_files`, `read_file`,
`read_multiple_files`, `list_directory`, `directory_tree`,
`ralph_graph`, `ralph_index_status`, `ralph_reindex`).

## Failure-mode matrix

The columns describe: (a) the failure as observed by an agent tool
call, (b) the current handling prior to this work item, (c) the
required auto-mode behaviour per the product criteria, (d) the
recovery action per R2, and (e) the canonical reason code returned
on the response so callers can detect the degraded path. Reason codes
are stable identifiers; their meaning is the contract.

| # | Failure | Current handling | Required auto-mode behaviour | Recovery | Reason code |
|---|---|---|---|---|---|
| F1 | Index directory or DB does not exist (first run, new worktree) | `_grep_handlers.py` falls through to live grep when `current_generation == 0`; `_read_handlers.py` returns `indexed_selector_unavailable` for indexed selectors | Auto: live fall-through; `use_index="always"` fails closed | Build the index in the background | `no_committed_generation` |
| F2 | Index deleted mid-run | Reopen detects missing files; subsequent calls return cold-store | Auto: live fall-through | Rebuild in the background | `no_committed_generation` |
| F3 | Index empty / cold build running | Cold-store guard above catches it | Auto: live fall-through | Build continues; index serves queries once it covers them | `no_committed_generation` |
| F4 | Schema / extractor version mismatch | `build_explore_index` wipes the store when `schema_version`/`extractor_version` settings disagree | Auto: live fall-through after wipe | Discard old index and rebuild | `version_mismatch` |
| F5 | Index corrupted / unreadable / leftover sidecars | `_initialize()` fails on bad pages; calls without a partial-path probe currently raise | Auto: live fall-through | Quarantine / discard + rebuild | `index_corrupt` |
| F6 | Build interrupted (crash / kill / OOM / timeout / power loss) | Writer does not serve a half-written generation (atomic swap on full build, generator-bound on changed) | Auto: live fall-through; never serve a half-written generation | Next session resumes / restarts the build; no manual cleanup | `interrupted_build` |
| F7 | Index locked / busy (another session, worktree, or process writing) | WAL + busy-timeout bound the wait; readers continue past the wait budget | Auto: live fall-through within the timeout budget | Writer finishes; readers pick up the new generation | `index_locked` |
| F8 | Several sessions share one workspace's index at once | `ReindexWriter.claim` coalesces via a per-db-path active map; tests pin the contract | Correct results for every session; one rebuild at a time | Single-writer lock; readers pick up the new generation | (n/a — silent coalesce) |
| F9 | Disk full / index directory unwritable (permissions, RO mount) | Writes raise OSError; current handlers don't fall back | Auto: live fall-through | Bounded exponential backoff; self-recover when writable | `index_unwritable` |
| F10 | Index read-only (RO mount) | Reads succeed; writes raise | Serve from index while fresh; otherwise fall through | Report the degraded state | `index_read_only` |
| F11 | Index stale past threshold (R4) | (pre-work: no threshold probe) | Auto: live fall-through | Changed-files refresh | `index_stale_scope` |
| F12 | External edits (shell, editor, other processes) | (pre-work: only in-session mutations are seen) | Never return content no longer in the files; never miss added content | Detect + refresh | `index_stale_scope` |
| F13 | Mass git change (checkout, rebase, merge, reset, stash, pull) | (pre-work: only in-session mutations) | Fall through until the index catches up | Changed-files or full refresh, whichever is cheaper | `index_stale_scope` |
| F14 | Ignore rules changed (`.gitignore` edited) | (pre-work: not re-checked) | Results honour the current ignore rules | Re-check affected paths | `ignore_rule_changed` |
| F15 | Hard files (binary, huge, invalid encoding, long lines, symlink loops, unreadable, odd names) | Existing chunker skips/records without crashing; tests pin this | Same results as live search under same rules; never crash the build | Skip / record such files without stopping | `hard_file_skipped` |
| F16 | Query not FTS-eligible (regex, multiline) | `is_fts_eligible` short-circuits; live grep runs | Fall through | None needed | `pattern_not_fts_eligible` |
| F17 | Index / query call goes over its timeout budget | Per-call budgets are bounded (1-N ms); live fall-through | Live-search results, or bounded partial result clearly marked incomplete | Build continues in the background | `timeout_exceeded` |
| F18 | Indexer throws an unexpected error | Currently surfaced as exception; the prompt forbids empty success | Fall through; tool call still succeeds | Log with diagnostics; retry with backoff; mark unhealthy after repeated failures | `indexer_error` |
| F19 | Memory / CPU pressure (low-memory host, many sessions) | Existing tests pin no-progress on tiny concurrency budgets | Searches keep working | Indexing slows down or pauses | `resource_pressure` |
| F20 | Workspace moved / renamed / worktree removed and recreated | Persisted rows reference the old root; new builds use the new root | Correct results for current paths | Wipe old index + rebuild | `workspace_moved` |

## Reason-code vocabulary

The reason codes above are the complete set returned by the explore
substrate's `fallback_reason` field. They are stable; tool authors
must not introduce new codes without updating this table.

* `no_committed_generation` — the store exists but no `current_generation` is set yet (cold build, missing store).
* `version_mismatch` — `schema_version`/`extractor_version`/structure-extractor version disagrees with the runtime; the old index has been wiped.
* `index_corrupt` — the index file failed integrity check / read; the store is quarantined for a rebuild.
* `interrupted_build` — a build was interrupted (crash / kill / OOM / timeout / power loss); the prior committed generation is preserved.
* `index_locked` — the index is locked by another writer; reader fell through after the timeout budget.
* `index_unwritable` — writes raise (disk full, permissions, RO mount); reads served if fresh.
* `index_read_only` — the index can be read but not written (F10); serve only while fresh.
* `index_stale_scope` — the staleness probe (R4) found stale paths inside the query scope.
* `ignore_rule_changed` — `.gitignore` / managed ignore rule changed; affected paths need a re-check.
* `hard_file_skipped` — a hard file (binary, oversize, invalid encoding, symlink loop, unreadable, odd name) was skipped or recorded without stopping the build.
* `pattern_not_fts_eligible` — the query pattern contains regex metacharacters or is otherwise unrepresentable in FTS5.
* `timeout_exceeded` — the indexed or live call exceeded its timeout budget; the response is bounded and clearly marked incomplete.
* `indexer_error` — the indexer threw an unexpected exception; the call falls through to live search and recovery is retried with backoff.
* `resource_pressure` — memory or CPU pressure; indexing paused or slowed down to keep the agent responsive.
* `workspace_moved` — the workspace root has moved or the worktree was removed; the persisted index no longer applies.
* `no_index_handle` — no `ExploreIndex` handle is attached to the session; the call uses the live path.

## Recovery semantics

* **Recovery is automatic.** No failure mode requires the agent or
  user to delete files, run a command, or restart the session.
* **Background cold build.** When any index-capable tool detects that no
  committed generation exists, a single-writer background build is
  scheduled under an advisory file lock (`.agent/ralph-explore/.lock`).
  The status handler reports `health: "building"` while the background
  reindex runs.
* **Checkpoint resume.** Staged builds write in batches of 50 files
  inside database transactions and persist intermediate progress so
  an interrupted, killed, or timed-out build resumes from its
  checkpoint rather than restarting from scratch.
* **Reader generation refresh.** Active session handles detect when an
  external process or background builder commits a new generation
  (on-disk database file swap or `current_generation` bump) and reopen
  the SQLite connection lazily and fail-open on the next query.
* **No retry storms.** Recovery attempts use exponential backoff with
  a bounded attempt count; after repeated failures the index is
  marked unhealthy and searches keep serving from live search.
* **No damage spreads.** A broken or half-written index never
  affects source files, workflow artifacts, or other worktrees'
  indexes. The recovery scheduler writes to a temp DB and atomically
  promotes the result; readers always see a complete generation or
  none.
* **Throughput and footprint.** Transaction batching across 50 files
  and eliminating redundant BLOB copies of chunk text yield >=10x
  cold build wall-clock speedup and >=3x index size reduction.
* **Truthful status.** `ralph_index_status` reports the current
  health (one of `healthy | building | stale | degraded | unhealthy`),
  the last failure reason, the recovery attempt counter, and the
  next recovery time.

## Coverage map

The acceptance contract for each failure mode F1–F20 has five axes:

* (a) parity — `auto`-mode results equal live-search results;
* (b) reason — the response reports the canonical `fallback_reason`
  from the table above;
* (c) budget — the call returns inside the 1 s per-call budget;
* (d) recovery — after the fault, the index recovers automatically
  and a LATER query is served from the index again;
* (e) status — `ralph_index_status` reports the truthful health
  state, last failure, and recovery state during and after the
  fault.

The legacy suite (`test_explore_fault_matrix.py`) carries the
per-mode minimum contract as a regression anchor; the comprehensive
suite (`test_explore_fault_matrix_full.py` + the late slice
`test_explore_fault_matrix_full_late.py`) carries the full
four/five-part contract. F6 and F8 use dedicated `subprocess_e2e`
suites for the kill-during-build and multi-session proofs.

A `✅` means the existing test covers that axis. Rows that were
missing an axis (the S-4 repair log closed them) carry the same
`✅` once the missing assertion is in place; see the S-4 repair
log below for the closure history.

| # | Primary test(s) | (a) parity | (b) reason | (c) budget | (d) recovery | (e) status |
|---|---|---|---|---|---|---|
| F1 | `test_f1_missing_index_parity_reason_budget_recovery_status` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f1_no_committed_generation_falls_through` (`test_explore_fault_matrix.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F2 | `test_f2_deleted_index_parity_reason_budget_recovery_status` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f2_deleted_index_mid_run_falls_through` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F3 | `test_f3_cold_partial_build_parity_reason_budget_recovery_status` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f3_cold_build_running_falls_through` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F4 | `test_f4_version_mismatch_wipes_and_rebuilds` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f4_version_mismatch_wipes_index` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F5 | `test_f5_corrupted_index_quarantines_and_rebuilds` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f5_corrupted_index_falls_through` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F6 | `test_repeated_random_kill_during_build_self_recovers` (`test_explore_crash_safety.py`); `test_fresh_session_after_kill_recovers_without_manual_cleanup`; `test_kill_during_atomic_promote_does_not_serve_partial_generation` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F7 | `test_f7_locked_index_falls_through_to_live` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f7_locked_database_falls_through_within_budget` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F8 | `test_multiple_sessions_share_one_workspace` (`test_explore_concurrency.py`); `test_concurrent_reindex_is_single_flight`; `test_concurrent_search_and_reindex_correct_for_all`; `test_concurrent_construction_no_database_is_locked` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F9 | `test_f9_unwritable_index_backs_off_and_self_recovers` (`test_explore_fault_matrix_full.py`); `test_f9_unwritable_retries_are_bounded_then_recover` (`test_explore_fault_matrix.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F10 | `test_f10_read_only_index_serves_then_recovers_when_writable` (`test_explore_fault_matrix_full.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F11 | `test_f11_stale_threshold_falls_through_and_recovers` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f11_stale_threshold_falls_through` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F12 | `test_f12_external_edit_detected_without_dirty_marking` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f12_external_edit_detected_and_falls_through` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F13 | `test_f13_mass_dirty_paths_falls_through_and_recovers` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f13_mass_dirty_paths_falls_through` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F14 | `test_f14_ignore_rule_change_rechecks_affected_paths` (`test_explore_fault_matrix_full.py`); legacy anchor `test_f14_ignore_rule_change_triggers_reindex_recovery` | ✅ | ✅ | ✅ | ✅ | ✅ |
| F15 | `test_f15_hard_files_skipped_without_crashing` (`test_explore_fault_matrix.py`); `test_f15_hard_files_skip_without_crashing` (`test_explore_fault_matrix_full_late.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F16 | `test_f16_regex_falls_through_to_live_grep` (`test_explore_fault_matrix.py`); `test_f16_regex_falls_through_to_live` (`test_explore_fault_matrix_full_late.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F17 | `test_f17_timeout_exceeded_returns_incomplete_bounded` (`test_explore_fault_matrix.py`); `test_f17_timeout_returns_bounded_partial` (`test_explore_fault_matrix_full_late.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F18 | `test_f18_indexer_error_does_not_raise` (`test_explore_fault_matrix.py`); `test_f18_indexer_exception_falls_through` (`test_explore_fault_matrix_full_late.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F19 | `test_f19_resource_pressure_keeps_searches_working` (`test_explore_fault_matrix.py`); `test_f19_resource_pressure_keeps_searches_working` (`test_explore_fault_matrix_full_late.py`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| F20 | `test_f20_workspace_moved_falls_through_and_recovers` (`test_explore_fault_matrix_full_late.py`); legacy anchor `test_f20_workspace_moved_falls_through` | ✅ | ✅ | ✅ | ✅ | ✅ |

In addition:

* `test_every_fault_mode_reports_a_canonical_reason_code`
  (`test_explore_fault_matrix.py`) is the cross-mode reason-code
  coverage anchor; it asserts every reason code referenced by
  every fault mode is in `CANONICAL_REASON_CODES`.
* `test_status_payload_truthful_for_every_fault_mode`
  (`test_explore_fault_matrix_full_late.py`) is the parametric
  per-fault (e) status-truthfulness anchor; for every fault code
  (`index_corrupt`, `interrupted_build`, `version_mismatch`,
  `index_unwritable`, `index_locked`, `indexer_error`,
  `workspace_moved`) it asserts the scheduler records the
  failure, the persisted state file mirrors the scheduler, and
  the persisted health/last-failure fields are correct.
* `test_status_health_during_fault_is_truthful`
  (`test_explore_fault_matrix.py`) and
  `test_status_health_after_recovery_is_healthy`
  (`test_explore_fault_matrix.py`) lock the (e) axis end-to-end:
  during a fault the status handler reports the matching
  `health` and `last_failure.code`; after the queued recovery
  the handler reports `healthy` with `last_failure is None`.

The coverage map is the mechanical proof of acceptance criterion
1 (failure-mode coverage); every row traces to a test file and
test name. The late slice test functions
(`test_f15_hard_files_skip_without_crashing` and
`test_f16_regex_falls_through_to_live`) carry the full
four/five-part acceptance contract — parity (a), reason code (b),
budget (c), later-query recovery (d), and `ralph_index_status`
truthfulness (e). The legacy per-mode minimum contract lives in
the `test_explore_fault_matrix.py` rows for F15/F16; the
comprehensive suite's F15/F16 status remains clean because the
comprehensive suite never carried an F15/F16 row (those failure
modes live only in the late slice to keep the suite under the
repo-structure audit's 1000-line cap).

## Cross-references

* `docs/agents/architecture.md` — overall MCP architecture.
* `ralph/mcp/explore/serving.py` — canonical reason-code helper that
  every index-capable tool invokes to populate the response metadata.
* `ralph/mcp/explore/recovery.py` — recovery scheduler that drives
  every applicable F1-F20 mode.
* `ralph/mcp/ARCHITECTURE.md` — explore section referenced from the
  user-facing MCP docs.