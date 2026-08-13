# Filesystem Activity Traceability

## Purpose

This is the maintained closure map for the filesystem-proportional run plan. It records the public boundary, current evidence, and remaining gap for each criterion in `.agent/PRODUCT_CRITERIA.md`. A `GAP` is not a waived requirement: it is the next implementation target. Re-prove the current baseline with:

```text
uv run pytest -q tests/test_filesystem_activity_baseline.py tests/test_fs_workspace_idempotent_write.py tests/agents/test_workspace_watch_scoping.py
uv run python -m ralph.testing.audit_filesystem_write_consolidation
uv run python -m ralph.testing.audit_filesystem_read_consolidation
uv run python -m ralph.testing.audit_filesystem_polling_invocation
```

## Matrix

| Criterion | Public boundary | Current evidence | Status / gap |
|---|---|---|---|
| W1 | `FsWorkspace.write` | `tests/test_fs_workspace_idempotent_write.py`; `tests/test_filesystem_activity_baseline.py` | COVERED for this boundary; audit prevents raw stable writers. |
| W2 | `FsWorkspace.write`; `WorkspaceMonitor.start` | `test_filesystem_activity_baseline.py` replays unchanged and changed cycles | COVERED for the characterized cycle; broader operations remain GAP. |
| W3 | Record/log writers | `tests/test_logging_file_buffering.py`; `tests/test_raw_overflow.py` | COVERED for bounded engine sinks, raw overflow buffering, and rendered-record batching; broader producer inventory remains GAP. |
| W4 | Stable persistence writers | `tests/test_idempotent_write.py`; `tests/test_idempotent_write_bytes.py` | COVERED for stable text/byte publication; inventory time-varying payloads and deliberate exceptions remains GAP. |
| W5 | Atomic artifact persistence and Explore staged-index publication | `tests/test_atomic_write_if_changed.py` cleanup cases; `tests/test_explore_pipeline.py::test_mode_full_swap_io_failure_preserves_committed_generation` | COVERED for canonical artifact transient cleanup and Explore pre-publication failure cleanup; other transient paths are GAP. |
| W6 | Atomic artifact persistence, checkpoint, and Explore staged-index publication | `tests/test_atomic_write_if_changed.py` (identical replay skips directory sync); `tests/test_checkpoint_idempotent.py` (changed checkpoint publication syncs its directory; identical replay skips it); `tests/test_explore_pipeline.py::test_mode_full_swap_io_failure_preserves_committed_generation` | COVERED for canonical atomic publication and checkpoint durability, including Explore's use of its pre-publication failure boundary; durability policy inventory is GAP. |
| W7 | History, cache, and run directories | `tests/unit/test_storage_lifecycle.py` (AST-derived writer discovery over `ralph/workspace/`, `ralph/mcp/artifacts/`, and `ralph/mcp/explore/` asserts every accumulating-path literal maps to an `inventory_storage` row with `active_owner`) | COVERED for the characterized accumulating paths and their policies; newly introduced writers must extend the inventory. |
| W8 | Engine-internal stores | `tests/agents/test_workspace_watch_scoping.py` classifier boundary cases | COVERED for the explicit finite engine-internal drop set; receipts remain source-visible run state. |
| R1 | `Workspace.snapshot`; MCP `read_file`; `FsWorkspace.read_lines` | `tests/test_tool_workspace_handle_read_file.py::test_full_read_reuses_one_snapshot_for_metadata_and_content`; `tests/test_workspace_fs_fs_workspace_read_lines.py::TestFsWorkspaceReadLines::test_read_lines_regression_does_not_probe_metadata_before_its_content_observation`; read audit | COVERED for the full-file tool request and line reads: each uses one content observation rather than composing a metadata probe with a later read; broader reader inventory remains GAP. |
| R2 | `FsWorkspace.iter_files` | shared skip set and read audit; `tests/test_workspace_fs_fs_workspace_iter_files.py::test_iter_files_does_not_follow_a_symlink_cycle` | COVERED for symlink cycle via `os.walk` default non-follow; broader traversal-owner coverage remains GAP. |
| R3 | `FsWorkspace.read_lines` and `read_bytes` | `tests/test_workspace_fs_fs_workspace_read_lines.py`; `tests/test_workspace_fs_fs_workspace_read_bytes.py` | COVERED for bounded line and byte windows; broader reader-call-site inventory remains GAP. |
| R4 | `Workspace.snapshot`; MCP `read_file` | `tests/test_tool_workspace_handle_read_file.py::test_full_read_reuses_one_snapshot_for_metadata_and_content`; read audit | COVERED for the full-file tool request: metadata and content share one observation; broader probe/read inventory remains GAP. |
| R5 | Explore index and workspace traversal | `tests/test_explore_pipeline.py::test_warm_no_op_reindex_parses_zero_files`; `tests/test_explore_pipeline.py::test_warm_no_op_does_not_duplicate_rows`; `tests/test_explore_pipeline.py::test_warm_small_edit_reparses_only_changed_file`; `tests/test_explore_bench_gates.py::test_no_op_reindex_parses_zero_files` | COVERED for warm no-op and dirty-path incremental index cycles; broader traversal-owner reuse remains GAP. |
| P1 | `WorkspaceMonitor.start`; `WorkspaceMonitor.awareness_status` | `tests/agents/test_workspace_watch_scoping.py`; baseline test | COVERED for one recursive watch shared by in-process leases, a `watch_capacity_predicted` pre-flight that skips kernel-side recursive expansion when the combined per-user watch prediction reaches the host budget, and the existing post-schedule EMFILE/ENOSPC fallback as the racy-case backstop; cross-process coordination is GAP. |
| P2 | `WorkspaceChangeClassifier` | `tests/agents/test_workspace_watch_scoping.py` classifier cases | COVERED for the current standing classifier exclusions; expand when new engine-internal paths are introduced. |
| P3 | Watch and poll lifecycle owners | `tests/agents/test_workspace_watch_scoping.py`; `tests/test_audit_filesystem_polling_invocation.py`; event-driven workspace monitor | COVERED structurally: raw timer polling fails verification unless a local bounded-lifecycle reason is present. |
| P4 | `WorkspaceMonitor.stop` | `tests/agents/test_workspace_watch_scoping.py` failure/release tests; `tests/test_audit_filesystem_polling_invocation.py` | COVERED for monitor and enforced ownership; each exceptional poll documents its release-bound lifecycle. |
| B1 | Public workspace paths and bytes | `tests/test_filesystem_activity_baseline.py` text/byte publication fixtures | COVERED for text and byte publication observability and unchanged replay. |
| B2 | Logging and artifact streams | `tests/test_logging_file_buffering.py`; `tests/test_raw_overflow.py` | COVERED for current buffered stream byte preservation; full artifact-history inventory remains GAP. |
| B3 | Atomic publication helper, checkpoint, and Explore full reindex | `tests/test_atomic_write_if_changed.py` (atomic replace and sync); `tests/test_idempotent_write.py`; `tests/test_checkpoint_idempotent.py` (directory sync on changed checkpoint publication and skip on replay); `tests/test_explore_pipeline.py::test_mode_full_swap_io_failure_preserves_committed_generation` | COVERED for helper and checkpoint durability behavior and Explore staged-index recovery from a pre-publication failure; all durability callers are GAP. |
| B4 | Atomic staging helper and Explore full reindex | `tests/test_atomic_write_if_changed.py::test_atomic_write_concurrent_writers_publish_independent_final_bytes`; `tests/test_atomic_write_if_changed.py::test_atomic_write_concurrent_identical_writers_skip_redundant_publications`; `tests/test_explore_pipeline.py::test_mode_full_swap_io_failure_preserves_committed_generation` | COVERED for in-process helper publication and Explore's fail-safe pre-publication recovery through the canonical primitive; cross-process publication remains GAP. |
| B5 | Live stream writers | `tests/test_raw_overflow.py::test_time_based_flush`; `tests/test_raw_overflow.py::test_close_flushes_and_reopen_appends` | COVERED for injected-clock periodic visibility and completion flush at current stream boundaries; exceptional-owner inventory remains GAP. |
| B6 | Shared retention state | `tests/unit/test_agent_dir_retention.py::test_sweep_consults_shared_active_run_registry`; `tests/unit/test_agent_dir_retention.py::test_sweep_consults_ownership_map_for_scratch_and_codex_home` | COVERED for shared active-run and temporary-path ownership protection across processes. |
| D1 | Write/read/polling consolidation audits | `tests/test_audit_filesystem_write_consolidation.py`; `tests/test_audit_filesystem_read_consolidation.py`; `tests/test_audit_filesystem_polling_invocation.py`; `tests/test_audit_fsevents_watch_consolidation.py` (all wired into `ralph.verify`) | COVERED for audited raw accesses, polling, watch construction, and direct process selection. |
| D2 | Audit diagnostics | `tests/test_audit_filesystem_write_consolidation.py`; `tests/test_audit_filesystem_read_consolidation.py` (actionable messages and tests) | COVERED for write/read audits. |
| D3 | Local audit markers | `tests/test_audit_filesystem_write_consolidation.py` (marker parsing); `tests/test_audit_filesystem_write_fail_closed.py`; `tests/test_audit_filesystem_read_consolidation.py` | COVERED for write/read markers; validate existing markers behaviorally. |
| E1 | Persistence and watch fake boundaries | `tests/test_filesystem_criterion_catalog.py` asserts every `COVERED` row in this matrix references a real test path on disk (the inverse invariant — `GAP` rows carry no test path — is preserved) | COVERED: the catalog test is the only proof; a COVERED flip without a real test on disk fails the gate. W8 and B6 remain GAP. |
| E2 | Regression tests | `tests/test_audit_regression_test_elimination.py`; `ralph.testing.audit_regression_test_elimination` | COVERED: declared eliminations must point to an existing test module. |
| E3 | Documentation | `scripts/fabrication_guard.py` (no unverified quantitative claim appears in this page) | COVERED. |
| E4 | Verification budget | `tests/test_verify_invariants.py`; `tests/test_verify_budget_real_time.py` (injected fakes and project test budget) | COVERED for current baseline; recheck full gate after every slice. |

## Workspace, watch, and storage inventory

| Area | Consumer / owner | Scope and lifecycle | Exclusions or retention | Failure behavior and user value |
|---|---|---|---|---|
| Project content | `FsWorkspace` | Caller-scoped reads and idempotent writes | Workspace root validation; unchanged content skips publication | Preserves user bytes; callers receive the underlying operation failure. |
| Workflow records | `.agent` artifact/state owners | Per run; `sweep_agent_dir` protects the active run | Aged receipts, sentinels, scratch, session metadata, and DB rows are swept after seven days (configurable via `[general] retention_max_age_days`; impossible values are rejected at config load) | Best-effort cleanup preserves active recovery data. |
| Workspace intelligence | `ExploreStore` | Workspace-scoped SQLite index; single reindex writer | `.agent/ralph-explore/` is disposable; jobs and tombstones have age/count caps | Last committed generation remains usable when refresh fails. |
| Operational records | log/raw writers | Buffered per run | Existing bounded sink/overflow policies | Buffered visibility and completion flush preserve diagnostics. |
| Temporary data | `.agent/tmp` retry/Codex homes | Active run only; recovery/startup sweep | Age-based removal; active run excluded | Best-effort cleanup removes interrupted work without affecting project content. |

| Watch consumer | Scope / start | Release and sharing | Exclusions | Capacity failure |
|---|---|---|---|---|
| `WorkspaceMonitor` | One recursive root subscription per canonical workspace when the first in-process lease starts; Linux pre-flight rejects a predicted per-user watch-budget breach before observer construction | Final lease stops and joins the observer; repeated starts are idempotent | Classifier drops `.git`, virtualenvs, `.agent/tmp`, `.agent/raw`, `.agent/artifacts`, state DB, logs, and caches from source activity | `awareness_status` reports `live_fallback`, including `watch_capacity_predicted` before registration; EMFILE/ENOSPC remains the racy-case backstop, and later leases retry observation. |

## S-1 Baseline

`tests/test_filesystem_activity_baseline.py` drives a workspace write and monitor lifecycle through an in-memory `FileBackend` and observer boundary. A first cycle publishes `alpha` and registers one recursive root watch. Repeating the same cycle preserves final bytes without another publication, directory preparation, or watch registration. A changed cycle publishes `beta` while retaining the same watch. This establishes the initial W1/W2/P1 characterization without real filesystem, observer, subprocess, or clock activity.

## Canonical boundaries and local exceptions

Stable full-file publication goes through `ralph.mcp.artifacts.idempotent_write` or
`FsWorkspace.write`. A compare-before-write skip also skips deferred parent-directory
creation, temporary staging, replacement, and the optional directory durability barrier.
Atomic publication stages beside the destination with a unique name, then replaces the
destination; callers opt into `sync_directory=True` only when their crash-recovery
contract requires the directory entry to be durable. The retained destination path and
final bytes are unchanged.

`Workspace.snapshot` is the typed one-observation boundary for a request that needs
metadata and content together. `read_lines` obtains its size from the same opened stream
that supplies its window; `read_bytes` size-checks before reading and exposes only its
requested window. `Workspace.iter_files` is the canonical recursive
walk and applies `RECURSIVE_SKIP_DIRECTORY_NAMES`; raw reads, probes, and traversals are
rejected by the package-wide read audit unless a local
`# filesystem-read-ok: <reason>` explains the bounded boundary.

`WorkspaceMonitor` is the lifecycle owner of one recursive workspace-root watch. It
retains that watch across unchanged cycles and releases it on normal stop or startup
failure. Parallel in-process retention sweeps coalesce through
`RetentionPassCoordinator` (one inner pass per wave). The filesystem-backed active-run
registry (`register_active_run` / `unregister_active_run`) protects every registered
run's receipts, sentinels, and DB rows across processes; the temporary ownership map
also protects active retry scratch and Codex homes from the sweep. `ralph.testing.audit_filesystem_polling_invocation` rejects raw timer polling,
watchdog observer construction, and product-owned direct subprocess choices outside their
typed owners. A local `# filesystem-poll-ok: <reason>` marker requires a non-empty
bounded-lifecycle explanation; it is for unavoidable protocol keepalives, process-exit
waits, teardown escalation, and cross-process lock acquisition, not ordinary filesystem
polling.

All three filesystem audits fail closed for unreadable source, including non-UTF-8
modules, so a newly added production file cannot evade enforcement by being undecodable.
Write exceptions use the equivalent local `# filesystem-write-ok: <reason>` marker and
must identify the user-requested, append-only, transient, durability, or retention
contract at the call site.

## Verified workspace-awareness boundaries

The maintained runtime uses one process-local recursive watch per workspace,
shared by monitor leases. `WorkspaceAwareness` keeps at most 512 coalesced
paths, exposes `current`, `pending`, `partial`, `stale`, `unavailable`, or
`live_fallback`, and transfers observed paths to the persisted Explore dirty
queue at bounded lifecycle boundaries. `ReindexWriter` coalesces refreshes;
warm no-op reindexing parses zero files and a localized edit reparses only the
affected file. `inventory_storage` and `plan_cleanup` report all five storage
categories without mutating the workspace.

The deterministic proof set is:

```text
uv run pytest -q tests/test_filesystem_activity_baseline.py tests/agents/test_workspace_watch_scoping.py tests/test_explore_lifecycle.py tests/test_explore_pipeline.py tests/test_explore_bench_gates.py tests/unit/test_agent_dir_retention.py tests/unit/test_storage_lifecycle.py tests/test_cli_workspace_health.py
uv run ralph workspace-health
uv run python -m ralph.testing.audit_filesystem_read_consolidation
uv run python -m ralph.testing.audit_filesystem_write_consolidation
uv run python -m ralph.testing.audit_filesystem_polling_invocation
uv run python -m ralph.testing.audit_fsevents_watch_consolidation
```

Cross-process observer leasing remains an explicit future boundary: the current
process-local watcher owner does not claim cross-process watch sharing. Retention
ownership is coordinated separately through shared active-run and temporary-path maps. A constrained
host falls back visibly to `live_fallback` and keeps the last committed index
distinct from current live content.

## Documentation review note

This is an internal traceability and operator-reference page, rather than a
first-run route. It consolidates the durable filesystem contract, approved
boundaries, exception syntax, and verification commands in one place; it does
not duplicate onboarding documentation or add public-product claims. Unresolved
rows are intentionally retained as the implementation backlog so operators and
maintainers can distinguish present proof from planned closure.
