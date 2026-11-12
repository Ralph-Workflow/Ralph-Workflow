# Retired Rust Implementation — Pointer

The maintained implementation of Ralph Workflow is the Python package under
[`ralph-workflow/`](../../ralph-workflow/). This page is the pointer to the
retired Rust implementation.

## Where the Rust implementation lives

The retired Rust implementation is the Cargo workspace the repository carried
at its root: the `mcp-server` crate plus the `ralph-workflow`,
`ralph-workflow-policy`, `test-helpers`, `tests`, and `xtask` members.

No Rust source is present in the working tree — `git ls-files` returns no
`.rs` file, no `Cargo.toml`, and no `Cargo.lock`. Read the implementation from
git history at the commit that ends the Rust workspace, `1f3fe086c`:

```bash
git show 1f3fe086c^:Cargo.toml
git show 1f3fe086c^:mcp-server/src/lib.rs
```

## Retained Rust material in this repository

- [`ralph-workflow/ralph/guidelines/rust.py`](../../ralph-workflow/ralph/guidelines/rust.py) —
  the Rust code-quality and security checklists that
  `ralph.guidelines.stack` selects when the detected stack language is Rust.

## Current behavior

The Python package is authoritative for current behavior. Read current
behavior and procedures in:

- Repo-root [`README.md`](../../README.md) — public storefront
- [`START_HERE.md`](../../START_HERE.md) — first-run walkthrough
- [`ralph-workflow/docs/sphinx/`](../../ralph-workflow/docs/sphinx/index.rst) —
  maintained operator manual
