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

For a clean review, set `status: no_issues`, omit the remediation sections,
and include concrete `## Review Evidence` rather than relying on an unsupported
clean claim:

```markdown
---
type: issues
status: no_issues
---

## Summary

- [SUM-1] The implementation satisfies the reviewed requirements.

## Review Evidence

- [E-1] Plan compliance | Every plan requirement and acceptance criterion was checked against the implementation.
- [E-2] Security | No security-sensitive surface changed; input and secret handling were inspected.
```

## Frontmatter

- `type` — required; `issues`.
- `status` — required and closed: `issues_found` or `no_issues`. Any other
  value, including `done` or `wrong`, is a hard error whose diagnostic names both
  accepted values.

## Sections

- `## Summary` — required; exactly one item.
- `## Review Evidence` — one item per checked dimension or requirement, shaped
  `dimension or requirement | concrete evidence`. A clean review should make
  this section non-empty.
- `## Issues` — items shaped `path | severity | summary`. `high`, `medium`,
  and `low` are recommended, while other non-empty descriptive severities are
  preserved.
- `## What Came Up Short` — one item per gap.
- `## How To Fix` — one item per concrete remediation step.

When `status: issues_found`, `## Issues`, `## What Came Up Short`, and
`## How To Fix` must all be present and non-empty. When `status: no_issues`,
omit those three remediation sections.

Unknown descriptive frontmatter fields and sections are accepted and ignored
by the typed issues consumer. Known `type`, `status`, and section shapes remain
strict.

## Hard errors vs warnings

Hard errors: `issues_found` without non-empty Issues, What Came Up Short,
and How To Fix; an Issues item not shaped `path | severity | summary`;
duplicate item IDs; unknown `status`; any grammar violation. Descriptive
severity labels are accepted without rewriting.
