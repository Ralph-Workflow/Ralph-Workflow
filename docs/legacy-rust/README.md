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
- `plans/` — Design and implementation plans from the Rust era, archived
  after the Python rewrite. The maintained Python-era plans remain in
  `docs/plans/`.
- `RFC/` — Rust-era request-for-comment process artifacts
  (RFC-001 through RFC-012).
- `tooling/` — Rust-era tooling notes (`dylint`, `remote-build`). The
  maintained Python tooling notes remain in `docs/tooling/python-tooling.md`.

## Cross-reference policy

When reading a legacy page, cross-check every claim against the maintained
manual and Python source. If you find a contradiction, **the Python
implementation is authoritative**.