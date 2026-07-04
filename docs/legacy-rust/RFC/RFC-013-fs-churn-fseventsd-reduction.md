# RFC-013: Filesystem Churn Reduction for Long-Running Workflows (fseventsd)

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


**RFC Number**: RFC-013
**Title**: Filesystem Churn Reduction for Long-Running Workflows (fseventsd)
**Status**: Review
**Author**: Claude (Fable 5), commissioned by Mistlight
**Created**: 2026-07-03

> NOTE: RFCs are historical design documents.
> Companion implementation plan: `ralph-workflow/docs/plans/2026-07-03-fs-churn-reduction.md`.

---

## Abstract

Long multi-instance Ralph Workflow runs on macOS drive the system's
`fseventsd` daemon to a sustained full CPU core. The cause is not one
bug but the aggregate filesystem-event volume of how the engine writes:
per-line open/append/close cycles on raw agent logs, line-buffered
logging sinks, and one-file-per-event bookkeeping that accumulates
without bound — multiplied by several concurrent instances and
amplified by two environmental factors on the external work volume
(Spotlight indexing and a pathologically bloated fsevents journal).

This RFC proposes three engine-side layers of remediation — (1) batch
the hot write paths, (2) add retention sweeps for accumulated
bookkeeping, (3) consolidate machine-only bookkeeping into a single
WAL-mode SQLite database per workspace — plus operator-level
mitigations for the environmental half. It also defines the boundary
of what must **not** move into a database.

## Motivation

When Ralph Workflow runs for hours-to-days, `fseventsd` CPU usage
spikes and stays high, degrading the whole machine. Operators observe
this as a host-level symptom with no obvious owner. We need to know
which events Ralph generates, which of them are avoidable, and whether
an embedded database should replace any file-based state.

## Evidence (measured 2026-07-03, live host)

Six concurrent `ralph` instances (some running since Jun 30) on an
external APFS volume (`/Volumes/Crucial X9`):

| Observation | Value | Healthy baseline |
|---|---|---|
| `fseventsd` CPU / RSS | 102% (>1 core, `top` convention) / 8.0 GB | ~0% / tens of MB |
| `fseventsd` cumulative CPU since Jun 24 | 1,383 min | — |
| `.fseventsd` journal on the volume | 29,121 files / 1.0 GB | a few MB |
| Spotlight (`mdutil -s`) on the volume | Indexing **enabled** | off for scratch volumes |
| `mds_stores` cumulative CPU | 627 min | — |
| Project-under-test `log/test.log` growth | ~80–120 KB/s per instance | — |
| Live `.agent/raw/*.log` sizes | up to 10.5 MB, growing | — |
| `.agent/completion_seen_*.json` accumulated | ~170 files (50 B each) over ~2 days | 0–1 |
| `.agent/receipts/<run_id>/` dirs accumulated | 176 | current run only |

A 30-second sample of files modified under the projects root showed:
Ralph's raw overflow logs and MCP server logs appending continuously;
the projects under test writing test logs, Active-Storage temp blobs,
and git objects; and one workspace regenerating hundreds of static
docs HTML files in a single verify cycle.

## Root-cause analysis

### Engine-owned event sources (fixable in Ralph)

1. **`RawOverflowLog.append()` opens and closes the file per line**
   (`ralph/display/raw_overflow.py:47`). A raw overflow log captures
   the full stdout of one *work unit* — a single agent invocation —
   at `.agent/raw/<unit_id>.log`. Every agent stdout line pays
   `mkdir` + `open("ab")` + `write` + `close`. All four construction
   sites funnel here (`subprocess_executor.py:99`,
   `_process_reader.py:225`, `_pty_line_reader.py:1061`,
   `parallel_display.py:1244`). At observed sizes (10 MB logs, ~100–200
   bytes per NDJSON line) this is on the order of 10⁵ open/close
   cycles over one unit's multi-hour lifetime; each cycle emits
   filesystem events beyond the write itself. This is the single
   worst engine-owned pattern.

2. **Loguru file sinks are line-buffered** (`buffering=1`, verified in
   the vendored loguru `FileSink.__init__`). One write syscall per log
   record per sink (main text log, optional structured JSONL,
   per-worker sinks).

3. **One-file-per-event bookkeeping, never swept.** Completion
   sentinels (`.agent/completion_seen_<run_id>.json`, one per agent
   session), receipts (`.agent/receipts/<run_id>/<artifact_type>.json`,
   one directory per run), and retry scratch
   (`.agent/tmp/agent_retry_*.md`). Individually tiny; collectively an
   unbounded stream of create events and growing directory listings
   that other subsystems (workspace-change classifier, watchdog
   evidence scans) then have to walk.

4. **Per-phase create/delete cycles (verified benign).** Prompt
   materialization rewrites several `.md` files per phase; drain
   clearing (deleting a phase's declared output artifacts — its
   "drains" — on fresh phase entry) removes a few files per phase;
   checkpoints use a `.tmp`+rename pair. These are correct designs at
   sane frequency — listed for completeness, they are *not* churn
   drivers (checkpoint saves are per phase transition, not per event;
   verified against `ralph/pipeline/reducer.py` and `runner.py`).

Explicitly ruled out: `.ralph/run.json` (written by Ralph-Workflow-Pro,
read-only to the engine per the Pro contract in
`ralph/pro_support/marker.py`); `mcp-server.log` (inherited-fd append,
one open per server start — a correct pattern).

### Environment-owned amplifiers (fixable by the operator)

5. **Spotlight indexes the work volume.** Every churned path is also
   re-examined by `mds`/`mds_stores`.

6. **The volume's fsevents journal is pathological.** 1.0 GB across
   29k journal files. `fseventsd` maintains this journal on every
   event; at this size the daemon itself degrades. It needs a one-time
   reset, and optionally `no_log` to stop journaling on that volume
   entirely.

### Driven-but-not-owned churn

7. **Verify loops make the projects under test churn.** Test logs at
   ~100 KB/s, tmp blobs, git objects, full docs rebuilds. Ralph
   triggers this by design (it runs the project's tests); the files
   belong to the target project. Only operator guidance applies.

## Proposed Changes

### P1. Batch the hot write paths (engine)

* `RawOverflowLog` holds one buffered file handle per unit
  (64 KB userspace buffer) with a time-based flush, plus explicit
  `close()` at unit/reader teardown.
  * **Hard invariant:** the idle watchdog uses raw-log growth as a
    liveness signal (`LOG_GROWTH_SECONDS = 30.0`,
    `ralph/timeout_defaults.py:250`), and its probe reads
    `RawOverflowLog.size_bytes` — so `size_bytes` must keep advancing
    per `append()` from an in-memory counter, independent of flushing.
    Buffering must never make a live unit look wedged.
  * **Soft bound:** the flush interval (default 5 s) is kept well
    below 30 s as defense-in-depth — it bounds operator `tail -f`
    latency and keeps the on-disk copy a valid liveness proxy should
    any future consumer read file size/mtime instead of `size_bytes`.
* Per-worker and structured-JSONL loguru sinks move to
  `buffering=8192`. The operator-facing `ralph.log` stays
  line-buffered so `tail -f` remains live.
  * **Accepted trade-off:** on SIGKILL, up to one buffer of log tail
    is lost. Clean exits and `logger.remove()` flush fully.

### P2. Retention sweeps (engine)

A best-effort, never-raising sweep at run start deletes bookkeeping
older than 7 days (always keeping the current run's entries):
completion sentinels, receipt directories, retry scratch. This bounds
both the create-event backlog visible to future scans and the
directory sizes that watchdog/classifier code walks.

### P3. Embedded database for machine-only bookkeeping (engine)

**Decision: yes to SQLite, with a strict scope boundary.**

A single WAL-mode SQLite database at `.agent/state.db` (stdlib
`sqlite3`, no new dependencies) becomes the store for:

* artifact **receipts** — `(run_id, artifact_type, hmac)` rows;
* **completion sentinels** — `(run_id, hmac)` rows.

Rationale:

* Both are *cross-process* (the MCP server writes, the engine reads);
  WAL mode with a busy timeout handles this on a local filesystem.
* Both are *machine-only*: no agent or human ever reads them as files.
* Writes become row upserts against one inode instead of
  `mkdir`+create per event; cleanup becomes `DELETE WHERE created_at <
  cutoff` instead of directory sweeps; atomicity is native instead of
  `.tmp`+rename.
* HMAC anti-forgery semantics are preserved unchanged. Background:
  receipts exist so the completion gate can trust that an artifact was
  really submitted through the MCP broker (the server process that
  mediates artifact submission). Each receipt carries an HMAC keyed by
  a broker-owned secret that is never exposed to the agent, so an
  agent with workspace write access cannot *forge* a valid receipt —
  it can at worst *delete or corrupt* state, which fails closed (gate
  stays unsatisfied). Moving rows into `state.db` changes none of
  this: corruption of the DB is equivalent to deleting receipt files
  today — denial, not forgery.

**What must NOT move into the database** (the scope boundary this RFC
establishes):

* `PLAN.md`, prompts, artifact JSON, handoff Markdown, exec spill
  files — agents read these through workspace file tools and humans
  review them; plain files are the product contract.
* The checkpoint (`checkpoint.json`) — saved per phase transition
  (rare), and the inspectable atomic-rename file is part of the resume
  contract.
* Artifact history archives — a viable later candidate, but write
  rates are low (per submission); deferred until the receipts/sentinel
  migration proves out.

Migration/compat: one release of dual-read (DB first, legacy file
fallback) so an in-flight run surviving an upgrade still passes its
completion gate. Legacy receipt/sentinel files left behind are reaped
by the P2 retention sweep once older than the window, so no manual
cleanup step is needed when the fallback is removed. `state.db*`
(db/-wal/-shm) joins `CACHE_FILENAME_GLOBS` in the workspace-change
classifier — the list that tells the idle watchdog which file changes
are engine bookkeeping rather than real agent work; omitting it would
let `state.db` writes masquerade as workspace progress — and the
internal-path audit allowlist (`ralph/testing/audit_agent_internal_paths.py`),
which fails CI when engine-internal paths leak into agent-visible
surfaces.

### P4. Operator mitigations + diagnostics (environment)

Immediate, no code:

1. `sudo mdutil -i off "/Volumes/<work volume>"` — stop Spotlight on
   scratch/work volumes.
2. One-time journal reset: with runs stopped,
   `sudo rm -rf "/Volumes/<vol>/.fseventsd"`, then remount/reboot
   (fseventsd recreates a fresh, small journal on remount).
3. Optional, aggressive — do this together with step 2, before the
   remount: recreate the directory and drop the sentinel in one go,
   `sudo mkdir "/Volumes/<vol>/.fseventsd" && sudo touch
   "/Volumes/<vol>/.fseventsd/no_log"`. On remount fseventsd sees
   `no_log` and stops journaling events to disk for that volume
   (live FSEvents subscribers still work; Time Machine/Spotlight on
   that volume degrade to full rescans). Scratch volumes only.
4. Keep project-under-test logs bounded (log rotation or truncation in
   verify scripts).

Permanent: an `fs-health` diagnostic in `ralph/diagnostics` warns when
the workspace volume has Spotlight enabled or an fsevents journal over
50 MB, and the operator manual documents the mitigations.

## Implementation Priority

| Item | Effort | Impact | Priority |
|------|--------|--------|----------|
| P4 operator mitigations (Spotlight off, journal reset) | Minutes, no release | Very high — removes both environmental amplifiers | P0, today |
| P1 `RawOverflowLog` buffered handle + teardown close | Small (one class + 4 call sites) | High — kills the worst per-line pattern | P0 |
| P2 run-start retention sweep | Small (one new module + one hook) | Medium — bounds accumulation and scan costs | P1 |
| P1 loguru sink buffering | Trivial (two `logger.add` kwargs) | Low–medium (only when file logging enabled) | P1 |
| P3 `state.db` receipts + sentinels | Medium (new module, two API swaps, dual-read compat) | Medium — zero file creates for bookkeeping, native atomicity | P2 |
| P4 `fs-health` diagnostic + operator docs | Small | Medium — makes the environmental half visible to every operator | P2 |

## Alternatives Considered

* **Move `.agent` scratch to the OS temp dir / another volume.**
  Rejected: `fseventsd` is one daemon for all volumes, so relocation
  does not reduce total event processing; and exec spills must stay
  inside the workspace root because agents' workspace-scoped read
  tools reject paths outside it (`_exec_output_spill.py` docstring).
* **A database for everything under `.agent/`.** Rejected: breaks the
  agent-readable/human-reviewable file contract; the event win beyond
  P3's scope is marginal while the migration risk is not.
* **An in-process write-behind daemon/queue for all logs.** Rejected as
  overbuilt: buffered handles with time-based flush achieve the same
  syscall batching without new moving parts or crash-loss windows
  beyond one buffer.
* **Reduce agent output instead of batching it.** Rejected: raw
  overflow logs exist precisely to preserve full NDJSON for watchdog
  corroboration and post-hoc debugging; dropping data to save events
  inverts the priorities.
* **fseventsd exclusions per directory.** Not available on macOS
  (exclusions are per-volume via `no_log` only); Spotlight’s
  `.metadata_never_index` does not affect fseventsd.

## Risks & Mitigations

* **Buffered raw logs delay on-disk visibility by up to the flush
  interval.** Bounded to 5 s by default; the watchdog liveness reads
  the in-memory counter, and 5 s ≪ the 30 s staleness threshold.
  Operators tailing a raw log see ≤5 s latency.
* **SQLite on unusual filesystems.** WAL requires a filesystem with
  working shared-memory/locking semantics; local APFS/ext4 are fine.
  Network filesystems (NFS/SMB workspaces) could misbehave — the
  legacy-file fallback path doubles as the escape hatch, and the
  migration should gate on a successful `PRAGMA journal_mode=WAL`.
* **Two writers, one DB.** MCP server and engine both open
  `state.db`; the busy timeout (5 s) must exceed any realistic write
  burst. Receipts/sentinels fire a handful of times per phase, so
  contention is negligible by construction.
* **Partial rollout confusion.** During the dual-read release, a
  receipt may exist as a file while its run continues on the DB path.
  Presence checks read DB-then-file, so behavior is monotone; deletes
  must hit both.

## Success Criteria

Measured under load comparable to the observed incident — 4–6
concurrent runs active for several hours (30 minutes with ≥2 runs is
the minimum smoke check, not the acceptance bar):

* After P1–P2 + operator mitigations: `fseventsd` CPU in single
  digits; `.fseventsd` journal stays in the low MB after reset.
* No watchdog regressions: `LOG_STALE_WHILE_ALIVE` corroboration keeps
  firing correctly for wedged agents (existing timeout suites pass).
* `.agent/` no longer accumulates unbounded sentinels/receipts/scratch
  across runs.
* For P3 specifically: receipt/sentinel activity creates zero new
  files under `.agent/` (only `state.db*` changes), and one full
  release of dual-read soak shows no completion-gate misses in either
  direction (DB-written receipts honored; legacy-file receipts from
  the prior release honored).

If `fseventsd` still spikes after P1–P4, profile with
`sudo fs_usage -w -f filesys` scoped to the ralph PIDs before
concluding more engine work is needed — at that point the residual is
most likely project-under-test churn (item 7), which no engine change
can remove.

## Rollout

1. P4 operator mitigations (immediate, reversible, no release).
2. P1 + P2 in one release (pure perf, no format changes).
3. P3 in the following release with dual-read compat; remove the
   legacy-file fallback one release later.

Task-level breakdown with TDD steps, code, and exact file paths:
`docs/plans/2026-07-03-fs-churn-reduction.md`.

## References

* Implementation plan (task-level TDD breakdown, complete code):
  `ralph-workflow/docs/plans/2026-07-03-fs-churn-reduction.md`
* Hot path: `ralph-workflow/ralph/display/raw_overflow.py` (writer);
  construction sites in `ralph/agents/subprocess_executor.py`,
  `ralph/agents/invoke/_process_reader.py`,
  `ralph/agents/invoke/_pty_line_reader.py`,
  `ralph/display/parallel_display.py`
* Watchdog liveness contract: `ralph-workflow/ralph/timeout_defaults.py`
  (`LOG_GROWTH_SECONDS`)
* Bookkeeping being consolidated:
  `ralph-workflow/ralph/mcp/artifacts/completion_receipts.py`,
  `ralph-workflow/ralph/mcp/tools/coordination.py`
  (`COMPLETION_SENTINEL_RELPATHFMT`)
* Ruled out: checkpoint cadence (`ralph/pipeline/reducer.py`,
  `ralph/pipeline/runner.py`), Pro marker
  (`ralph/pro_support/marker.py`)

## Open Questions

1. Should the retention window (7 days) and raw-log flush interval
   (5 s) be exposed in `general_config` rather than constants?
   (Plan currently ships constants; config plumbing is cheap to add
   later if operators ask.)
2. Should `fs-health` warnings escalate to a startup banner when the
   journal exceeds, say, 200 MB — or stay diagnostics-only?
3. Artifact history archives (`ralph/mcp/artifacts/history.py`) write
   an archive copy plus an index rebuild per submission. Migrate to
   `state.db` in a follow-up RFC once P3 has soaked?
4. Does Ralph-Workflow-Pro want to share `state.db` (it already ships
   its own `ralph-monitor.db`), or must the Pro↔engine boundary stay
   file-based per the existing contract?
