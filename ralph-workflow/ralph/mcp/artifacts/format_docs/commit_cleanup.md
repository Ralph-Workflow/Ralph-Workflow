# commit_cleanup artifact format

You are recommending cleanup actions so binaries, build artifacts, and
machine-local files do not get committed. Author markdown and submit with
`ralph_submit_md_artifact` (`artifact_type: commit_cleanup`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/commit_cleanup.md`

## Complete minimal example

```markdown
---
type: commit_cleanup
analysis_complete: true
---

## Actions

- [A-1] delete_file | build/output.exe
- [A-2] add_to_gitignore | *.pyc
- [A-3] add_to_git_exclude | .env.local
```

## Frontmatter

- `type` — required; `commit_cleanup`.
- `analysis_complete` — required; exactly `true` or `false`. Set `true`
  when nothing more needs cleaning.

## Sections

- `## Actions` — required (may be empty when `analysis_complete: true`).
  Each item is `action | value`:
  - `delete_file | <path>` — remove a file from the repo.
  - `add_to_gitignore | <pattern>` — project-wide ignore pattern.
  - `add_to_git_exclude | <pattern>` — machine-local exclude pattern.
- `## Reason` — optional, at most one item explaining the decision.

## Hard errors vs warnings

Hard errors: `analysis_complete` not literally `true`/`false`; an Actions
item without ` | `; an unknown action name; and the security guards on
`path`/`pattern` values — they must be printable ASCII (no whitespace,
control characters, or non-ASCII) and must not start with `#`
(comment-line injection into `.gitignore`/`.git/info/exclude`). No
warning-level coercions exist for this type.

## What is safe to delete

Only obvious build artifacts in the diff and Ralph runtime artifacts
(e.g. `checkpoint.json`, `.agent/PLAN.md`, `.agent/tmp/*.log`,
`.agent/artifacts/*.md`). Never delete source code, tests, docs, or
intentionally modified configuration, and never recommend deleting whole
directories — the runtime safety boundary silently drops unsafe deletes.
Use `add_to_gitignore` for project-wide patterns and `add_to_git_exclude`
for machine-local ones.
