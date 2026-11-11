# commit_review artifact format

You are recommending cleanup actions and providing a safety verdict so binaries,
build artifacts, machine-local files, and accidental damage do not get committed.
Author markdown and submit with `ralph_submit_md_artifact` (`artifact_type: commit_review`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/commit_review.md`

## Complete minimal example

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
```

## Frontmatter

- `type` — required; `commit_review`.
- `verdict` — required; `approve` or `reject`.
- `tree_sha` — required; 40-character hex string representing the sealed git tree SHA.

## Sections

- `## Actions` — required (may be empty).
  Each item is `action | value`:
  - `delete_file | <path>` — remove a file from the repo.
  - `add_to_gitignore | <pattern>` — project-wide ignore pattern.
  - `add_to_git_exclude | <pattern>` — machine-local exclude pattern. When
    the pattern selects a tracked recognized secret (`.env`, `.env.local`,
    `.env.*.local`, `secrets.yml`, or `credentials.json`), Ralph removes that
    secret from the index while retaining the working-tree file. Arbitrary
    project patterns never trigger automatic untracking.
- `## Reason` — required when `verdict` is `reject` (must contain at least one item
  citing concrete evidence such as paths or counts); optional when `verdict` is `approve`.

Unknown descriptive frontmatter fields and sections are accepted and ignored
by the typed review consumer. Known fields, action names, and action value
guards remain strict.

## Hard errors vs warnings

Hard errors: `verdict` not literally `approve` or `reject`; `tree_sha` not a 40-hex
string; missing or empty `## Reason` when `verdict` is `reject`; missing `## Actions`;
an Actions item without ` | `; an unknown action name; and the security guards on
`path`/`pattern` values — they must be printable ASCII (no whitespace,
control characters, or non-ASCII) and must not start with `#`
(comment-line injection into `.gitignore`/`.git/info/exclude`). No
warning-level coercions exist for this type.

## What is safe to delete

Only obvious build artifacts in the diff and Ralph runtime artifacts
(e.g. `checkpoint.json`, `.agent/PLAN.md`, `.agent/tmp/*.log`,
`.agent/artifacts/*.md`). Never delete source code, tests, docs, or
intentionally modified configuration, and never recommend deleting whole
directories — the runtime safety boundary declines unsafe deletes, logs the
decline, and loops the run back to commit review with a retry hint instead
of advancing to commit. Use `add_to_gitignore` for project-wide patterns
and `add_to_git_exclude` for machine-local ones.
