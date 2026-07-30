---
name: submit-commit-cleanup-artifact
description: Use when submitting a commit_cleanup artifact as markdown via ralph_submit_md_artifact with delete_file / add_to_gitignore / add_to_git_exclude actions, or when an action was rejected by the printable-ASCII rule or silently dropped by the security boundary
version: 2.1.0
---

# submit-commit-cleanup-artifact

## Overview

A commit cleanup is one markdown document
(`artifact_type: "commit_cleanup"`) recommending which files must NOT be
committed: binaries, build artifacts, editor temp files, machine-local
configuration.

Submit with `ralph_submit_md_artifact`; pre-check with
`ralph_verify_md_artifact`.

## Document Shape

Frontmatter: `type: commit_cleanup` and `analysis_complete: true` or
`analysis_complete: false` (exactly those lowercase words).

| Section | Required | Items |
|---|---|---|
| `## Actions` | yes (may have zero items) | `<action> \| <path-or-pattern>` per item |
| `## Reason` | no | at most 1: why these actions |

Each action item text is `<action> | <value>` with a space-pipe-space
separator:

- `delete_file | <path>` — remove a file from the repo. Only for actual
  binary/generated files in the diff or paths on the Ralph
  runtime-artifact allowlist.
- `add_to_gitignore | <pattern>` — project-wide patterns like `*.pyc`,
  `build/`, `dist/`.
- `add_to_git_exclude | <pattern>` — machine-local patterns like
  `.env.local`, `.vscode/`. Never use `delete_file` for environment files.

Paths and patterns must be printable ASCII: no control characters, no
whitespace-only values, no non-ASCII, and no leading `#` (a `#` prefix
would silently disable a real `.gitignore` / `.git/info/exclude` rule via
comment-line injection — the validator rejects it on purpose).

## Security Boundary (keep these semantics)

The runtime enforces a path boundary on top of validation; actions that
cross it are silently dropped, not applied:

- NEVER `delete_file` source code, tests, documentation, or configuration
  — even when tracked in HEAD.
- NEVER recommend blanket `.agent/`, `artifacts/`, or `reports/`
  directory deletion; only specific allowlisted basenames and
  per-directory extensions are deletable.
- Ralph runtime artifacts ARE unconditionally deletable. The allowlist is:
  the canonical `.agent/` top-level runtime basenames (e.g. `.agent/PLAN.md`,
  `.agent/ISSUES.md`, `.agent/PRODUCT_CRITERIA.md`, `.agent/checkpoint.json`,
  `.agent/mcp.toml`, the `*_RESULT.md` / `*_ANALYSIS_DECISION.md` files),
  `.agent/completion_seen_*.json`, bare `checkpoint.json` at the repo root,
  and files inside engine-internal directories ONLY when the extension
  matches that directory's engine-written types: `.agent/raw/` (`.log`
  only), `.agent/tmp/` (`.log`/`.md`/`.json`), `.agent/artifacts/` (`.md`),
  `.agent/receipts/<run-id>/` (`.json`), `.agent/prompt_history/`
  (`.json`), `.agent/artifact-formats/` (`.md`), `.agent/workers/<unit>/`
  (`.log`/`.md`/`.json` at any depth). Anything else under `.agent/` is
  user-authored — do not delete it.
- NEVER remove lock files (`package-lock.json`, `uv.lock`, `Cargo.lock`,
  `poetry.lock`, `go.sum`, …) — they are intentional committed manifests.
- Machine-local files go to `add_to_git_exclude`, project-wide junk
  patterns to `add_to_gitignore`.

## Core Flow

1. Inspect the pending diff. If nothing needs cleanup, submit
   `analysis_complete: true` with an empty `## Actions` section.
2. Otherwise write one action item per cleanup, respecting the security
   boundary above.
3. `ralph_submit_md_artifact({"artifact_type": "commit_cleanup", "content": ...})`.

Worked example:

```markdown
---
type: commit_cleanup
analysis_complete: true
---

## Actions

- [A-1] delete_file | build/output.exe
- [A-2] add_to_gitignore | *.pyc
- [A-3] add_to_git_exclude | .env.local

## Reason

- [R-1] Build output and bytecode are generated; .env.local is machine-local.
```

Nothing-to-clean example:

```markdown
---
type: commit_cleanup
analysis_complete: true
---

## Actions
```

## Error Recovery

- `analysis_complete must be 'true' or 'false'` — fix the frontmatter
  value; no other spellings are accepted.
- `Actions entries must use '<action> | <path-or-pattern>'` — the item
  text needs the ` | ` separator with a non-empty action and value.
- An unknown action word is rejected — the closed set is exactly
  `delete_file`, `add_to_gitignore`, `add_to_git_exclude`.
- Printable-ASCII / `#`-prefix rejections — replace the offending path or
  pattern; the constraint is a security guard, do not work around it.
- Actions silently dropped at commit time — you crossed the security
  boundary. Re-read the allowlist in the Security Boundary section above,
  narrow to allowlisted basenames/extensions, or reclassify as
  `add_to_git_exclude`.
