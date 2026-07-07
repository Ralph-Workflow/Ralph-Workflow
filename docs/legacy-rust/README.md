# Legacy Rust-era Documentation

> **Status:** UNMAINTAINED. Historical reference only.

These pages describe the **retired Rust implementation** of Ralph Workflow
(pre-Python rewrite, archived). They are kept for historical context and to
let readers trace architectural decisions back to their origins.

**They are NOT the current product.** For the maintained system see:

- Repo-root [README.md](../../README.md) — public storefront
- [START_HERE.md](../../START_HERE.md) — fastest first-run guide
- [ralph-workflow/docs/sphinx/](../../ralph-workflow/docs/sphinx/index.rst) — maintained operator manual

## Why these pages are quarantined

Ralph Workflow is now a Python package under `ralph-workflow/`. The previous
Rust runtime was archived during the Python rewrite (see
`docs/plans/2026-04-14-python-orchestration-parity-completion.md` for the
transition plan and
`docs/plans/2026-06-07-rust-mcp-sidecar-rework.md` for the sidecar transition).

Pages in this directory describe that retired runtime and may reference:

- `cargo`, `cargo xtask`, `cargo-dylint`, `Clippy`
- Rust crate layout, reducer/event-loop orchestration, effect system
- `RwBuildServer`, `RwBuildServer-2` (the retired build servers)
- Pre-Python artifact paths and CLI flags

Do not act on any guidance here without first confirming it is consistent with
the current Python implementation.

## Subdirectories

- `performance/` — Performance notes for the retired Rust runtime
  (monitoring, optimization, memory budgets, baselines).
- `architecture/` — Architecture pages for the retired Rust runtime
  (checkpoint-and-resume, agents-and-prompts, git-and-rebase, etc.).
  Includes `code-style-architecture.md` (formerly misfiled under
  `docs/code-style/`, moved here as part of the legacy quarantine).
- `plans/` — Design and implementation plans from the Rust era, archived
  after the Python rewrite. The maintained Python-era plans remain in
  `docs/plans/`. As of 2026-07-07, the 6 plans in this directory
  remain intact — the docs cleanup audit (see
  `tmp/legacy-rust-decisions.md`) determined that every file was
  modified after the 2026-05-01 post-Python-rewrite boundary and was
  therefore retained by the keep rule.
- `RFC/` — Rust-era request-for-comment process artifacts
  (RFC-001 through RFC-013). As of 2026-07-07, all 13 RFCs remain
  intact; the three required override keeps (RFC-004 reducer-based
  pipeline architecture, RFC-005 event-loop savecheckpoint bypass,
  RFC-009 mcp-agent-orchestrator communication) are preserved per the
  audit decision file.
- `tooling/` — Rust-era tooling notes (`dylint`, `remote-build`). The
  maintained Python tooling notes remain in `docs/tooling/python-tooling.md`.

## Cleanup audit trail

The 2026-07-07 docs cleanup pass applied an executable keep/delete
rule to every RFC and plan in this directory. The audit decision log
lives in `tmp/legacy-rust-decisions.md`; the per-file decision table
shows every RFC and plan alongside its keep justification. No files
were deleted in this directory during the cleanup; the 19 files
present before the cleanup are still present, retained by the
post-2026-05-01 timestamp clause of the keep rule.

## Cross-reference policy

When reading a legacy page, cross-check every claim against the maintained
manual and Python source. If you find a contradiction, **the Python
implementation is authoritative**.

## Read this before opening anything in this directory

The pages that used to live under `docs/legacy-rust/` have been moved
out of the canonical docs tree into `tmp/legacy-rust-archive/` so they
cannot compete with the maintained Python docs for the reader's
attention or be picked up by current toctrees and linkcheckers. The
remaining pages under `docs/legacy-rust/` are intentionally tiny — they
exist only so historical cross-references still resolve to something
useful instead of 404'ing into the quarantined archive.

If you arrived here from a link in the maintained manual, treat every
word in the quarantined archive as a description of the retired Rust
implementation, not the maintained Python package. **Do not act on
any guidance here without first confirming it is consistent with the
current Python implementation.** When in doubt, the Python source
under `ralph-workflow/ralph/` and the maintained operator manual at
[`ralph-workflow/docs/sphinx/index.rst`](../../ralph-workflow/docs/sphinx/index.rst)
are the single source of truth.

The archived files are inventoried in `tmp/legacy-rust-archive/INDEX.md`,
which records the original relative path and line count for every file
that was moved. The archive itself is gitignored because the canonical
docs surface does not depend on it; anyone investigating the retired
runtime can `git log -- docs/legacy-rust/<file>` to trace the history
of any page, or read the moved copies directly from the working tree.