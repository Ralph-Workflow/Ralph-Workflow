# issues artifact format

You are reporting issues found during a review: what is wrong, where, and
how to fix it. Author markdown and submit with `ralph_submit_md_artifact`
(`artifact_type: issues`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/issues.md`

## Complete minimal example (issues found)

```markdown
---
type: issues
status: issues_found
---

## Summary

- [SUM-1] Input validation is missing on the auth endpoint.

## Issues

- [I-1] src/main.py | high | Missing input validation on login route

## What Came Up Short

- [W-1] No validation for user-supplied credentials.

## How To Fix

- [FIX-1] Add schema validation to the login handler and a regression test.
```

For a clean review, set `status: no_issues` and submit only `## Summary`.

## Frontmatter

- `type` — required; `issues`.
- `status` — required; `issues_found` or `no_issues`. An unknown value is
  coerced to `no_issues` with a warning.

## Sections

- `## Summary` — required; exactly one item.
- `## Issues` — items shaped `path | severity | summary` with severity one
  of high, medium, low.
- `## What Came Up Short` — one item per gap.
- `## How To Fix` — one item per concrete remediation step.

When `status: issues_found`, all three sections above must be present and
non-empty. When `status: no_issues`, omit them.

## Hard errors vs warnings

Hard errors: `issues_found` without non-empty Issues, What Came Up Short,
and How To Fix; an Issues item not shaped `path | severity | summary`;
duplicate item IDs; any grammar violation. Warnings: unknown `status`
coerced to `no_issues`; unknown severity coerced to `low`.
