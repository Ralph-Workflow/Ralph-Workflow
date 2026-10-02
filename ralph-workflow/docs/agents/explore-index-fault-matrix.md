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
* **No retry storms.** Recovery attempts use exponential backoff with
  a bounded attempt count; after repeated failures the index is
  marked unhealthy and searches keep serving from live search.
* **No damage spreads.** A broken or half-written index never
  affects source files, workflow artifacts, or other worktrees'
  indexes. The recovery scheduler writes to a temp DB and atomically
  promotes the result; readers always see a complete generation or
  none.
* **Truthful status.** `ralph_index_status` reports the current
  health (one of `healthy | building | stale | degraded | unhealthy`),
  the last failure reason, the recovery attempt counter, and the
  next recovery time.

## Cross-references

* `docs/agents/architecture.md` — overall MCP architecture.
* `ralph/mcp/explore/serving.py` — canonical reason-code helper that
  every index-capable tool invokes to populate the response metadata.
* `ralph/mcp/explore/recovery.py` — recovery scheduler that drives
  every applicable F1-F20 mode.
* `ralph/mcp/ARCHITECTURE.md` — explore section referenced from the
  user-facing MCP docs.