# commit_cleanup artifact format

## What you are doing

You are analyzing the current git state to identify files that MUST NOT be committed, such as binaries, build artifacts, editor temporary files, and machine-local configuration. You recommend actions to clean up the commit.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `"commit_cleanup"` and `content` set to a JSON string of your cleanup payload.

```json
{
  "artifact_type": "commit_cleanup",
  "content": "{\"analysis_complete\": false, \"actions\": [{\"action\": \"add_to_gitignore\", \"pattern\": \"*.exe\"}]}"
}
```

## Required fields (inside content)

- `analysis_complete` — boolean, set to `true` when there is nothing more to clean up or all cleanup is done
- `actions` — array of action objects (can be empty if analysis_complete is true)

Each action object must have:
- `action` — one of `"delete_file"`, `"add_to_gitignore"`, `"add_to_git_exclude"`
- `path` — required for `"delete_file"` action (the file to remove from the repo)
- `pattern` — required for `"add_to_gitignore"` and `"add_to_git_exclude"` actions

## Optional fields

- `reason` — string, an optional explanation for the cleanup decision

## Schema validation contract

`path` and `pattern` are validated by the Pydantic model `CommitCleanupAction` and must be **printable ASCII (one or more non-whitespace printable characters, no control chars, no whitespace-only values, no non-ASCII characters)**. Values starting with `#` are rejected at Pydantic validation time so a malformed artifact cannot silently disable a real `.gitignore` or `.git/info/exclude` rule via comment-line injection.

## Complete example — must contain a ```json block

```json
{
  "artifact_type": "commit_cleanup",
  "content": "{\"analysis_complete\": false, \"actions\": [{\"action\": \"delete_file\", \"path\": \"build/output.exe\"}, {\"action\": \"add_to_gitignore\", \"pattern\": \"*.pyc\"}, {\"action\": \"add_to_git_exclude\", \"pattern\": \".env.local\"}]}"
}
```

## Common mistakes

- Do NOT recommend deleting source files, test files, or documentation
- Do NOT recommend deleting configuration files that were intentionally modified
- Do NOT change the semantic meaning of the commit — only clean up obvious build artifacts
- Do NOT use `add_to_gitignore` for machine-local patterns; use `add_to_git_exclude` instead
- Do NOT delete files that are part of the actual commit (source code, tests, docs)

## Ralph runtime artifacts (ALWAYS safe to delete)

The following paths are Ralph Workflow runtime artifacts. They are unconditionally deletable from any commit, even when tracked in HEAD:

- Specific basenames at the `.agent/` top level: `.agent/CURRENT_PROMPT.md`, `.agent/PLAN.md`, `.agent/ISSUES.md`, `.agent/DEVELOPMENT_RESULT.md`, `.agent/FIX_RESULT.md`, `.agent/DEVELOPMENT_ANALYSIS_DECISION.md`, `.agent/PLANNING_ANALYSIS_DECISION.md`, `.agent/REVIEW_ANALYSIS_DECISION.md`, `.agent/checkpoint.json`, `.agent/rebase_checkpoint.json`, `.agent/rebase_checkpoint.json.bak`, `.agent/rebase.lock`, `.agent/start_commit`, `.agent/mcp.toml`
- Completion sentinels: any `.agent/completion_seen_*.json` (the filename glob is `completion_seen_*.json`, NOT `completion_sentinel_*.json`)
- Root-level: bare `checkpoint.json` at the repo root
- Files inside engine-internal directories ONLY when their extension matches the engine-written file types for that directory:
  - `.agent/raw/` — `.log` files only (e.g. `.agent/raw/opencode.log`)
  - `.agent/tmp/` — `.log`, `.md`, `.json` files (e.g. `.agent/tmp/mcp-server.log`, `.agent/tmp/<safe_id>.md`)
  - `.agent/artifacts/` — `.json` files (e.g. `.agent/artifacts/commit_cleanup.json`)
  - `.agent/receipts/<run-id>/` — `.json` files (e.g. `.agent/receipts/run-1/commit_cleanup.json`)
  - `.agent/prompt_history/` — `.json` files (e.g. `.agent/prompt_history/abc.json`)
  - `.agent/artifact-formats/` — `.md` files (e.g. `.agent/artifact-formats/commit_message.md`)
  - `.agent/workers/<unit>/...` — `.log`, `.md`, `.json` files at any depth (e.g. `.agent/workers/unit-a/tmp/checkpoint.json`)

## Security boundary

The directory allowlist above is RESTRICTED by file extension. Files under the listed directories with a different extension are user-authored content and MUST NOT be deleted, even when tracked in HEAD. Examples that MUST be rejected:

- `.agent/raw/script.py`, `.agent/raw/main.go`, `.agent/raw/notes.md` — only `.log` is deletable in `raw/`
- `.agent/tmp/config.yaml`, `.agent/tmp/main.py` — only `.log`, `.md`, `.json` are deletable in `tmp/`
- `.agent/artifacts/notes.md` — only `.json` is deletable in `artifacts/`
- `.agent/receipts/run-1/note.md` — only `.json` is deletable in `receipts/`
- `.agent/artifact-formats/data.json` — only `.md` is deletable in `artifact-formats/`
- `.agent/workers/unit-a/src/main.py`, `.agent/workers/unit-a/src/foo.go` — only `.log`, `.md`, `.json` are deletable in `workers/`

For source-code files at the `.agent/` top level that are NOT in the basenames list (e.g. `.agent/test.py`, `.agent/CHANGELOG.md`, `.agent/utils.py`, `.agent/scripts/build.sh`), and for arbitrary subdirectories under `.agent/` (e.g. `.agent/notes/foo.txt`, `.agent/data/seed.json`), DO NOT delete them — they are user-authored content even if they happen to live under `.agent/`. The same applies to source-code files anywhere else in the repo.

**Do NOT recommend broad `.agent/`, `artifacts/`, or `reports/` directory deletion** — only the specific basenames/extensions listed above are deletable. A blanket directory-prefix recommendation will be silently dropped by the runtime safety boundary.

## Engine safety contract

The `commit_cleanup` phase is hardened to be ROCK SOLID against any malformed or borderline cleanup batch:

- **Cleanup is best-effort.** A single unsafe `delete_file` action (one that targets source code, test files, documentation, configuration, or any other non-housekeeping path) does NOT abort the whole phase. The unsafe action is logged at WARNING level and accumulated in a returned `skipped_delete_paths` list; safe actions (other matching deletes, gitignore patterns, git-exclude patterns) continue to apply.
- **The phase only returns `PhaseFailureEvent` when EVERY delete action was rejected AND no safe action was applied.** In that case the event's `reason` field carries a structured retry hint naming every rejected path, so the agent can resubmit without those paths or reclassify them as `add_to_git_exclude`.
- **Canonical Ralph runtime artifact paths (the basenames and per-directory extensions above) are ALWAYS deletable** — even when tracked in HEAD. The fast-path exemption in `is_agent_internal_path` runs as the FIRST executable statement of `_is_safe_to_delete`, so the engine-owned allowlist cannot be silently bypassed by a future refactor that adds a check above it.
- **The phase auto-seeds canonical Ralph patterns into `.gitignore` and `.git/info/exclude` on every entry** (not just bootstrap). The seed uses the real exported helpers `auto_seed_default_gitignore` and `auto_seed_default_git_exclude` from `ralph.config.bootstrap`; both calls are wrapped in `try/except Exception` so a seeding failure (e.g. read-only filesystem, missing gitdir) cannot fail the phase.
- **A workspace whose root cannot be resolved** (e.g. read-only filesystem, malformed workspace mock) returns `PhaseFailureEvent(recoverable=True, retry_in_session=True, failure_category=ARTIFACT_VALIDATION)` carrying the underlying cause; the phase does NOT silently fall back to `Path.cwd()` and corrupt an unrelated directory.
- **`.gitignore` and `.git/info/exclude` writes are atomic** (sibling-staging with an `id(payload)`-derived unique suffix + `Path.replace()` per the pattern from `ralph/mcp/transport/agy.py:99-127`) so SIGKILL during cleanup leaves the files intact.
- **`delete_file` rejects symlinks BEFORE `.resolve()`** (defense-in-depth at the lowest layer: `Path.resolve()` follows symlinks, so a symlink at `evil_link` would resolve to its sibling and the post-resolve `is_symlink()` check would not fire on the symlink itself). A failed `Path.unlink()` call propagates `OSError` so the caller's WARNING log records the real cause (the prior implementation swallowed `OSError` via `with suppress(OSError):`).
- **The commit executor's `_resolve_commit_scope` rejects symlinks, absolute paths, and `..` segments** at the boundary so a malformed commit payload cannot stage files outside the worktree via a symlink chain. The check walks every parent directory in the literal path AND resolves the final candidate with `strict=False`, so a parent-directory symlink chain escape (e.g. `linkdir/file.txt` where `linkdir` is a symlink to outside the worktree) is rejected before `git add` follows the symlink.

## Canonical proofs

The rock-solid cleanup behavior is pinned by the integration tests:

- `tests/integration/test_commit_cleanup_rock_solid.py` — unit-level proof that `handle_commit_cleanup_phase` cleans up all five originally-failing tracked paths AND preserves a distinct-path innocent symlink, in a single deterministic run.
- `tests/integration/test_pipeline_commit_cleanup_to_commit_e2e.py` — black-box proof that the cleanup -> commit transition works end-to-end through the real `runner.run` harness.
- `tests/integration/test_pipeline_final_commit_cleanup_to_final_commit_e2e.py` — black-box proof that the final-commit cleanup -> final-commit transition works the same way.
- `tests/integration/test_pipeline_with_engine_internal_artifacts.py` — the canonical existing proof that the three originally-failing tracked paths (`checkpoint.json`, `.agent/raw/opencode.log`, `.agent/tmp/mcp-server.log`) clean up end-to-end.

## Dumb-proof checklist

- Did you set `artifact_type` to `"commit_cleanup"`?
- Did you set `analysis_complete` to `true` when there is nothing more to clean?
- Did you use `delete_file` only for actual binary/generated files present in the diff OR for paths in the Ralph runtime-artifact allowlist above?
- Did you cross-check the Security boundary to avoid recommending `.agent/<dir>/<wrong-extension>` or arbitrary `.agent/<sub>/...` paths?
- Did you use `add_to_gitignore` for project-wide patterns like `*.pyc`?
- Did you use `add_to_git_exclude` for machine-local files like `.env.local`?
- Did you NOT recommend deleting source code, test files, or documentation?
- Did you NOT recommend broad `.agent/`, `artifacts/`, or `reports/` directory deletion?
