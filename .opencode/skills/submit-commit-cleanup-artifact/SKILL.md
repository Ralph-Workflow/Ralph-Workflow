---
name: submit-commit-cleanup-artifact
description: Use when submitting a commit_cleanup artifact with an actions array distinguishing delete_file, add_to_gitignore, and add_to_git_exclude via ralph_submit_artifact, or when the actions array was rejected by the security-boundary check and you need to recover the runtime-artifact allowlist
---

# submit-commit-cleanup-artifact

## Overview

This is an **OPTIONAL** skill that lives alongside the canonical
commit_cleanup format doc at
`.agent/artifact-formats/commit_cleanup.md`. Use it as a quick lookup
before submitting a commit_cleanup artifact, not as a substitute for the
format doc. The format doc is the source of truth for every required
field, the runtime-artifact allowlist matrix, and the security-boundary
rule.

**Skill name vs MCP tool name.** This skill is named
`submit-commit-cleanup-artifact`. It is a separate name from the generic
MCP tool `ralph_submit_artifact`, which is the active submission entry
point. Do not conflate the two: the MCP tool for commit cleanup is
`ralph_submit_artifact` with `artifact_type="commit_cleanup"`.

## When to Use

Use this skill when you are about to call `ralph_submit_artifact` to
report which files must NOT be committed (binaries, build artifacts,
editor temporary files, machine-local configuration). It is the right
skill for the canonical commit_cleanup artifact only — for plans,
development results, commit_message, issues, fix_result,
smoke_test_result, or analysis-decision artifacts, use the companion
`submit-artifact` skill instead.

## Core Flow (one-shot)

1. Read `.agent/artifact-formats/commit_cleanup.md` once. It defines the
   runtime-artifact allowlist matrix, the per-directory extension
   restrictions, the security-boundary rule, and the printable-ASCII
   contract for path/pattern strings. Treat it as a contract you must
   match exactly.
2. Decide whether cleanup is needed:
   - Nothing to clean up: set `analysis_complete=true` and `actions=[]`.
   - Cleanup needed: build the discriminated `actions` array.
3. Choose the right action discriminator for each entry:
   - `delete_file` — paired with a `path` (a file present in the diff or
     in the runtime-artifact allowlist). Use only for actual binary /
     generated files OR for paths in the runtime-artifact allowlist.
   - `add_to_gitignore` — paired with a `pattern`. Use for project-wide
     patterns like `*.pyc`, `build/`, `dist/`.
   - `add_to_git_exclude` — paired with a `pattern`. Use for
     machine-local files like `.env.local`, `.vscode/`. NEVER use
     `delete_file` for environment files.
4. Honor the runtime-artifact allowlist (see the
   `Runtime-artifact allowlist quick reference` subsection under
   `Common Mistakes` below). Files in the allowlist are unconditionally
   deletable.
5. Honor the security boundary. Files outside the allowlist that are
   source code, tests, documentation, or configuration MUST NOT be
   deleted; the runtime silently drops such actions.
6. Build the inner payload as a plain JSON object:
   `{"analysis_complete": <bool>, "actions": [<action>, ...]}`.
7. Pass the inner payload as `content` either as the native JSON object
   or as a JSON-serialized string. Do NOT wrap it in an outer `{"type": ..., "content": ...}`
   envelope — Ralph Workflow adds artifact metadata itself.
8. Call
   `ralph_submit_artifact({"artifact_type": "commit_cleanup", "content": {"analysis_complete": true, "actions": []}})`.
9. After the submit success text, call `ralph_declare_complete(summary="commit_cleanup")`.

**Minimal one-shot happy-path envelope** for an empty cleanup:

```json
{
  "artifact_type": "commit_cleanup",
  "content": "{\"analysis_complete\": true, \"actions\": []}"
}
```

**Minimal one-shot happy-path envelope** for a multi-action cleanup:

```json
{
  "artifact_type": "commit_cleanup",
  "content": "{\"analysis_complete\": true, \"actions\": [{\"action\": \"delete_file\", \"path\": \"build/output.exe\"}, {\"action\": \"add_to_gitignore\", \"pattern\": \"*.pyc\"}, {\"action\": \"add_to_git_exclude\", \"pattern\": \".env.local\"}]}"
}
```

## Recovery from a Bad Payload

When `ralph_submit_artifact` rejects a `commit_cleanup` payload, the helper
`_raise_format_doc_error` raises an `InvalidParamsError` whose message
points at `.agent/artifact-formats/commit_cleanup.md` and names
`ralph_submit_artifact` as the retry tool. Read the message, then:

1. If the helper `_artifact_content_format_error` is raised, your payload
   is missing the `content` field. Re-issue the call with `content` set to
   a native JSON object or JSON-serialized string.
2. If validation complains about the `action` discriminator, you used a
   value other than `"delete_file"`, `"add_to_gitignore"`, or
   `"add_to_git_exclude"`. The closed enum has exactly three values.
3. If validation complains about a missing `path`, you supplied
   `action="delete_file"` but did not specify a `path`. Every delete_file
   action must have a `path`.
4. If validation complains about a missing `pattern`, you supplied
   `action="add_to_gitignore"` or `action="add_to_git_exclude"` but did
   not specify a `pattern`. Every gitignore / git-exclude action must have
   a `pattern`.
5. If validation complains about a `path` or `pattern` starting with `#`,
   having control characters, or being whitespace-only, you supplied a
   malformed value. The `CommitCleanupAction` Pydantic model enforces the
   printable-ASCII contract: one or more non-whitespace printable
   characters, no control chars, no whitespace-only values, no non-ASCII
   characters. A `#` prefix would silently disable a real `.gitignore` or
   `.git/info/exclude` rule via comment-line injection — the validator
   rejects it on purpose.
6. If the runtime safety boundary silently dropped your actions, you
   recommended paths outside the runtime-artifact allowlist (see the
   `Runtime-artifact allowlist quick reference` subsection under
   `Common Mistakes` below) OR you recommended a blanket `.agent/`,
   `artifacts/`, or `reports/` directory deletion. Re-narrow to specific
   basenames / per-directory extension restrictions and resubmit.

**Worked retry envelope** for a `_raise_format_doc_error` style failure on
`commit_cleanup`:

```json
{
  "artifact_type": "commit_cleanup",
  "content": "{\"analysis_complete\": true, \"actions\": [{\"action\": \"add_to_gitignore\", \"pattern\": \"*.pyc\"}]}"
}
```

## Source of Truth Reference

- `.agent/artifact-formats/commit_cleanup.md` — the canonical schema for
  the commit_cleanup artifact. Bundled by Ralph Workflow and materialized
  into the workspace on demand. Every required field, the runtime-artifact
  allowlist matrix, and the security-boundary rule are defined here.
- `.agent/artifact-formats/artifact_formats_index.md` — the index that
  lists every supported `artifact_type` (including `commit_cleanup`) and
  points to each format doc.

If this skill and the format doc ever disagree, the format doc wins.

## Common Mistakes

- Treating this skill as authoritative. The format doc at
  `.agent/artifact-formats/commit_cleanup.md` is the source of truth; this
  skill is a quick pointer, not a substitute.
- Conflating `submit-commit-cleanup-artifact` (this skill) with the MCP
  tool `ralph_submit_artifact`. The MCP tool is the active submission
  entry point; the skill is the passive reference document.
- Using a path or pattern that violates the security boundary. Files
  under the listed directories with a different extension are
  user-authored content and MUST NOT be deleted. Examples that MUST be
  rejected: `.agent/raw/script.py`, `.agent/tmp/config.yaml`,
  `.agent/artifacts/notes.md`, `.agent/receipts/run-1/note.md`,
  `.agent/artifact-formats/data.json`, `.agent/workers/unit-a/src/main.py`.
- Recommending broad `.agent/`, `artifacts/`, or `reports/` directory
  deletion. A blanket directory-prefix recommendation is silently dropped
  by the runtime safety boundary. Only the specific basenames /
  per-directory extensions listed in the
  `Runtime-artifact allowlist quick reference` subsection below are
  deletable.
- Using `delete_file` for source code, test files, documentation, or
  configuration files. Those categories MUST NOT be deleted, even when
  tracked in HEAD.
- Using `add_to_gitignore` for machine-local patterns like `.env.local`
  or `.vscode/`. Use `add_to_git_exclude` instead.
- Removing lock files (`package-lock.json`, `yarn.lock`, `Cargo.lock`,
  `poetry.lock`, `uv.lock`, `Pipfile.lock`, `composer.lock`,
  `Gemfile.lock`, `go.sum`). These are intentional committed dependency
  manifests.
- Using a `path` or `pattern` value starting with `#`. The validator
  rejects `#`-prefixed values to prevent silent comment-line injection
  into `.gitignore` or `.git/info/exclude`.
- Using a `path` or `pattern` value with control characters, whitespace
  only, or non-ASCII characters. The `CommitCleanupAction` Pydantic model
  enforces the printable-ASCII contract.
- Using `content_path` instead of `content`. Use `content` with a native JSON object
  or JSON-serialized string; `content_path` is reserved for non-agent
  callers.
- Treating a native JSON object as invalid. `content` may be a native JSON
  object or a JSON-serialized string; keep the outer
  `{"artifact_type": "commit_cleanup", "content": ...}` envelope either way.

### Runtime-artifact allowlist quick reference

The following paths are Ralph Workflow runtime artifacts. They are
unconditionally deletable from any commit, even when tracked in HEAD.
Recommend them with `delete_file` without hesitation.

**Top-level `.agent/` basenames (the canonical 14):**
`.agent/CURRENT_PROMPT.md`, `.agent/PLAN.md`, `.agent/ISSUES.md`,
`.agent/DEVELOPMENT_RESULT.md`, `.agent/FIX_RESULT.md`,
`.agent/DEVELOPMENT_ANALYSIS_DECISION.md`,
`.agent/PLANNING_ANALYSIS_DECISION.md`,
`.agent/REVIEW_ANALYSIS_DECISION.md`, `.agent/checkpoint.json`,
`.agent/rebase_checkpoint.json`, `.agent/rebase_checkpoint.json.bak`,
`.agent/rebase.lock`, `.agent/start_commit`, `.agent/mcp.toml`.

**Completion sentinels:** any `.agent/completion_seen_*.json` (the
filename glob is `completion_seen_*.json`, NOT
`completion_sentinel_*.json`).

**Root-level:** bare `checkpoint.json` at the repo root.

**Per-directory extension restrictions:** files inside engine-internal
directories are deletable ONLY when their extension matches the
engine-written file types for that directory:

| Directory | Deletable extensions | Example |
|---|---|---|
| `.agent/raw/` | `.log` | `.agent/raw/opencode.log` |
| `.agent/tmp/` | `.log`, `.md`, `.json` | `.agent/tmp/mcp-server.log`, `.agent/tmp/<safe_id>.md` |
| `.agent/artifacts/` | `.json` | `.agent/artifacts/commit_cleanup.json` |
| `.agent/receipts/<run-id>/` | `.json` | `.agent/receipts/run-1/commit_cleanup.json` |
| `.agent/prompt_history/` | `.json` | `.agent/prompt_history/abc.json` |
| `.agent/artifact-formats/` | `.md` | `.agent/artifact-formats/commit_message.md` |
| `.agent/workers/<unit>/...` | `.log`, `.md`, `.json` | `.agent/workers/unit-a/tmp/checkpoint.json` |

## Red Flags - STOP and Start Over

- "I have read the format doc so I do not need the skill." STOP. The
  skill is a per-tool retry envelope; the format doc is the schema. They
  cover different failure modes.
- "The skill is OPTIONAL therefore ignorable." STOP. The OPTIONAL marker
  means the agent may consult the skill, not that the agent may skip the
  source-of-truth format doc. The skill names the format doc explicitly.
- "I will use `delete_file` for source code, test files, documentation,
  or configuration." STOP. The security boundary silently drops such
  actions. Source code and tracked user-authored content MUST NOT be
  deleted, even when tracked in HEAD.
- "I will recommend broad `.agent/`, `artifacts/`, or `reports/`
  directory deletion." STOP. A blanket directory-prefix recommendation
  is silently dropped by the runtime safety boundary. Use the specific
  basenames / per-directory extensions listed in the
  `Runtime-artifact allowlist quick reference` subsection.
- "I will use `add_to_gitignore` for machine-local patterns like
  `.env.local` or `.vscode/`." STOP. Use `add_to_git_exclude` for
  machine-local patterns; `add_to_gitignore` is for project-wide
  patterns like `*.pyc`, `build/`, `dist/`.
- "I will remove a lock file (`package-lock.json`, `uv.lock`,
  `Cargo.lock`, etc.)." STOP. Lock files are intentional committed
  dependency manifests; deleting them silently breaks every
  contributor's `npm install` / `uv sync` / `cargo build` etc.
