# commit_message artifact format

You are writing the git commit message for the current changes, or skipping
the commit with a reason. Author it as markdown and submit with
`ralph_submit_md_artifact` (`artifact_type: commit_message`); lint first
with `ralph_verify_md_artifact` if unsure.

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/commit_message.md`

## Complete minimal example (commit)

```markdown
---
type: commit
subject: fix(auth): prevent token expiry race
---

## Body

- [B-1] Serialize refresh operations per token so a concurrent refresh cannot invalidate a token still in use.
```

## Complete minimal example (skip)

```markdown
---
type: skip
reason: No committable changes; only generated files were touched.
---
```

## Frontmatter

- `type` — required and closed: `commit` or `skip`. Any other value, including
  `done` or `wrong`, is a hard error. The diagnostic names both accepted
  values; correct the frontmatter and resubmit.
- `subject` — required for `commit`; must be a conventional-commit subject:
  `kind(scope)?!?: lowercase description` with kind one of feat, fix, docs,
  refactor, test, style, perf, build, ci, chore. An optional `!` before the
  colon marks a breaking change (`feat!: ...`, `feat(api)!: ...`).
  Frontmatter values are taken literally and must be unquoted — quotes
  become part of the value and fail subject validation.
- `reason` — required for `skip`.

## Sections (all optional; commit only)

- `## Body` — exactly one item: the whole commit body on one line.
- `## Body Summary` / `## Body Details` / `## Body Footer` — one item each;
  a structured alternative to `## Body` for complex changes. Never combine
  with `## Body`.
- `## Files` — one item per file to include; must be non-empty if present.
- `## Excluded Files` — items shaped `path | reason` with reason one of:
  internal_ignore, not_task_related, sensitive, deferred.

Most non-trivial commits should include a body explaining the why.

## Hard errors vs warnings

Hard errors: missing or non-conventional `subject`; missing `reason` on
skip; `type` not commit/skip; `## Body` combined with detailed body
sections; empty `## Files`; malformed `## Excluded Files` entries or an
unknown exclusion reason; any grammar violation (unknown section, stray
prose line, duplicate item ID). `done` and `wrong` are not aliases for either
valid type. Fix every diagnostic.
