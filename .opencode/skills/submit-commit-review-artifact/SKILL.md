---
name: submit-commit-review-artifact
description: Use when submitting a commit_review artifact as markdown via ralph_submit_md_artifact with delete_file / add_to_gitignore / add_to_git_exclude actions, or when an action was rejected by the printable-ASCII rule or declined by the security boundary
version: 2.2.0
---

# submit-commit-review-artifact

## Overview

A commit review is one markdown document (`artifact_type: "commit_review"`) providing a safety damage verdict and recommending which files must NOT be committed: binaries, build artifacts, editor temp files, and machine-local configuration.

Submit with `ralph_submit_md_artifact`; pre-check with `ralph_verify_md_artifact`.

## Document Shape

Frontmatter:
- `type: commit_review`
- `verdict: approve` or `verdict: reject` (exactly those lowercase words)
- `tree_sha: <40-hex string>` (the 40-character hex SHA of the sealed proposal tree)

| Section | Required | Items |
|---|---|---|
| `## Actions` | yes (may have zero items) | `<action> \| <path-or-pattern>` per item |
| `## Reason` | required on `verdict: reject`, optional on `verdict: approve` | at least 1 item on reject citing concrete damage evidence (paths/counts) |

Each action item text is `<action> | <value>` with a space-pipe-space separator:

- `delete_file | <path>` — remove a file from the repo. Only for actual binary/generated files in the diff or paths on the Ralph runtime-artifact allowlist.
- `add_to_gitignore | <pattern>` — project-wide patterns like `*.pyc`, `build/`, `dist/`.
- `add_to_git_exclude | <pattern>` — machine-local patterns like `.env.local`, `.vscode/`. Never use `delete_file` for environment files.

Paths and patterns must be printable ASCII: no control characters, no whitespace-only values, no non-ASCII, and no leading `#` (a `#` prefix would silently disable a real `.gitignore` / `.git/info/exclude` rule via comment-line injection — the validator rejects it on purpose).

## Security Boundary (keep these semantics)

The runtime enforces a path boundary on top of validation; actions that cross it are declined, not applied — the decline is logged and the run loops back to commit review with a retry hint naming the rejected paths, so the run never advances to commit:

- NEVER `delete_file` source code, tests, documentation, or configuration — even when tracked in HEAD.
- NEVER recommend blanket `.agent/`, `artifacts/`, or `reports/` directory deletion; only specific allowlisted basenames and per-directory extensions are deletable.
- Ralph runtime artifacts ARE unconditionally deletable. The allowlist is: the canonical `.agent/` top-level runtime basenames (e.g. `.agent/PLAN.md`, `.agent/ISSUES.md`, `.agent/PRODUCT_CRITERIA.md`, `.agent/checkpoint.json`, `.agent/mcp.toml`, the `*_RESULT.md` / `*_ANALYSIS_DECISION.md` files), `.agent/completion_seen_*.json`, bare `checkpoint.json` at the repo root, and files inside engine-internal directories ONLY when the extension matches that directory's engine-written types: `.agent/raw/` (`.log` only), `.agent/tmp/` (`.log`/`.md`/`.json`), `.agent/artifacts/` (`.md`), `.agent/receipts/<run-id>/` (`.json`), `.agent/prompt_history/` (`.json`), `.agent/artifact-formats/` (`.md`), `.agent/workers/<unit>/` (`.log`/`.md`/`.json` at any depth). Anything else under `.agent/` is user-authored — do not delete it.
- NEVER remove lock files (`package-lock.json`, `uv.lock`, `Cargo.lock`, `poetry.lock`, `go.sum`, …) — they are intentional committed manifests.
- Machine-local files go to `add_to_git_exclude`, project-wide junk patterns to `add_to_gitignore`.

## Core Flow

1. Inspect Stage 1 evidence (proposal ref, sealed tree SHA, soft tripwire findings) and the pending diff against HEAD.
2. If the diff shows accidental damage (mass deletions, protected files removed, unhandled secrets), set `verdict: reject` with specific citable reasons in `## Reason`.
3. If safe, set `verdict: approve` with the sealed proposal `tree_sha`, and list any necessary housekeeping actions in `## Actions`. If no housekeeping is needed, leave `## Actions` empty.
4. Submit with `ralph_submit_md_artifact({"artifact_type": "commit_review", "content": ...})`.

Worked example:

```markdown
---
type: commit_review
verdict: approve
tree_sha: 0123456789abcdef0123456789abcdef01234567
---

## Actions

- [A-1] delete_file | build/output.exe
- [A-2] add_to_gitignore | *.pyc
- [A-3] add_to_git_exclude | .env.local

## Reason

- [RS-1] Build output and bytecode are generated; .env.local is machine-local.
```

Nothing-to-clean example:

```markdown
---
type: commit_review
verdict: approve
tree_sha: 0123456789abcdef0123456789abcdef01234567
---

## Actions
```

Rejection example:

```markdown
---
type: commit_review
verdict: reject
tree_sha: 0123456789abcdef0123456789abcdef01234567
---

## Actions

## Reason

- [RS-1] Accidental deletion of 35 production source files under src/core.
```

## Error Recovery

- `verdict must be 'approve' or 'reject'` (SPEC010) — fix the frontmatter value; no other spellings are accepted.
- `frontmatter 'tree_sha' must be a 40-character hex string` (SPEC010) — provide the 40-hex SHA of the sealed proposal tree.
- `missing required section 'Reason' when verdict is 'reject'` (SPEC008) or `section 'Reason' requires list items when verdict is 'reject'` (SPEC006) — when rejecting, provide at least one list item in `## Reason` citing concrete evidence (paths or counts).
- `Actions entries must use '<action> | <path-or-pattern>'` — the item text needs the ` | ` separator with a non-empty action and value.
- An unknown action word is rejected — the closed set is exactly `delete_file`, `add_to_gitignore`, `add_to_git_exclude`.
- Printable-ASCII / `#`-prefix rejections — replace the offending path or pattern; the constraint is a security guard, do not work around it.
- Actions declined by the security boundary — you crossed it. The decline is logged and the run loops back to commit review with a retry hint naming the rejected paths; the run does not advance to commit. Re-read the allowlist in the Security Boundary section above, narrow to allowlisted basenames/extensions, or reclassify as `add_to_git_exclude`.
