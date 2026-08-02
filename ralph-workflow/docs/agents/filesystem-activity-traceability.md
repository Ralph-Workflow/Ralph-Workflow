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
| W3 | Record/log writers | Buffered logging and record-writer tests | GAP: inventory every high-frequency producer. |
| W4 | Stable persistence writers | Compare-before-write helpers | GAP: inventory time-varying payloads and document deliberate exceptions. |
| W5 | Atomic artifact persistence | `tests/test_atomic_write_if_changed.py` cleanup cases | COVERED for atomic helper; other transient paths are GAP. |
| W6 | Atomic artifact persistence | identical replay skips directory sync | COVERED for helper; durability policy inventory is GAP. |
| W7 | History, cache, and run directories | Existing retention owners | GAP: characterize every accumulating path and its policy. |
| W8 | Engine-internal stores | Existing workspace/run scoping | GAP: identify watched-tree internal state eligible for relocation. |
| R1 | `FileBackend`; `Workspace` | `audit_filesystem_read_consolidation` | GAP: behavioral per-operation read reuse inventory. |
| R2 | `FsWorkspace.iter_files` | shared skip set and read audit | GAP: symlink-cycle and all traversal-owner coverage. |
| R3 | `FsWorkspace.read_lines` and `read_bytes` | bounded read implementation/tests | GAP: all reader call sites and bounded snapshots. |
| R4 | `FileBackend.exists`; `Workspace.stat` | read-consolidation audit | GAP: eliminate repeated probe/read pairs behaviorally. |
| R5 | Explore index and workspace traversal | explore-index lifecycle tests | GAP: prove no-op index reuse rather than rewalk. |
| P1 | `WorkspaceMonitor.start` | `tests/agents/test_workspace_watch_scoping.py`; baseline test | COVERED for one monitor; cross-process coordination is GAP. |
| P2 | `WorkspaceChangeClassifier` | watch-scoping classifier tests | GAP: enumerate standing engine-internal exclusions. |
| P3 | Watch and poll lifecycle owners | event-driven workspace monitor; `audit_filesystem_polling_invocation` | COVERED structurally: raw timer polling fails verification unless a local bounded-lifecycle reason is present. |
| P4 | `WorkspaceMonitor.stop` | watch-scoping failure/release tests; `audit_filesystem_polling_invocation` | COVERED for monitor and enforced ownership; each exceptional poll documents its release-bound lifecycle. |
| B1 | Public workspace paths and bytes | baseline test final-content assertions | GAP: fixture comparison across all public outputs. |
| B2 | Logging and artifact streams | existing stream tests | GAP: full stream/history inventory. |
| B3 | Atomic publication helper | atomic replace and sync tests | COVERED for helper; all durability callers are GAP. |
| B4 | Atomic staging helper | unique staging-path regression | GAP: process-safe concurrent publication proof. |
| B5 | Live stream writers | existing flush/lifecycle tests | GAP: fake-clock live-latency comparison. |
| B6 | Shared persistence/watch state | unique staging path behavior | GAP: independent-process coordination proof. |
| D1 | Write/read/polling consolidation audits | all three audits are `ralph.verify` steps | COVERED for audited raw accesses, polling, watch construction, and direct process selection. |
| D2 | Audit diagnostics | actionable audit messages and tests | COVERED for write/read audits. |
| D3 | Local audit markers | marker parsing in write/read audits | COVERED for write/read markers; validate existing markers behaviorally. |
| E1 | Persistence and watch fake boundaries | baseline, idempotent-write, and watch-scoping tests | GAP: add black-box evidence for every matrix GAP as it closes. |
| E2 | Regression tests | unchanged replay and atomic-skip tests | GAP: each future elimination must have a revert-sensitive test. |
| E3 | Documentation | no quantitative claim is made here | COVERED. |
| E4 | Verification budget | injected fakes and project test budget | COVERED for current baseline; recheck full gate after every slice. |

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
metadata and content together. `read_lines` and `read_bytes` size-check before reading
and expose only their requested window. `Workspace.iter_files` is the canonical recursive
walk and applies `RECURSIVE_SKIP_DIRECTORY_NAMES`; raw reads, probes, and traversals are
rejected by the package-wide read audit unless a local
`# filesystem-read-ok: <reason>` explains the bounded boundary.

`WorkspaceMonitor` is the lifecycle owner of one recursive workspace-root watch. It
retains that watch across unchanged cycles and releases it on normal stop or startup
failure. `ralph.testing.audit_filesystem_polling_invocation` rejects raw timer polling,
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

## Next Step

Use the next unresolved row to create a red black-box test before changing production code. The highest-value current gap is an instrumented workspace observation boundary that can prove R1/R3/R4 behavior without host filesystem access.

## Documentation review note

This is an internal traceability and operator-reference page, rather than a
first-run route. It consolidates the durable filesystem contract, approved
boundaries, exception syntax, and verification commands in one place; it does
not duplicate onboarding documentation or add public-product claims. Unresolved
rows are intentionally retained as the implementation backlog so operators and
maintainers can distinguish present proof from planned closure.
