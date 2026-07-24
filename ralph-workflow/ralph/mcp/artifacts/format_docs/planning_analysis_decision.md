# planning_analysis_decision artifact format

You are reporting the outcome of a planning analysis review: whether the
plan is executor-ready, needs changes, or must be redone. Author markdown
and submit with `ralph_submit_md_artifact`
(`artifact_type: planning_analysis_decision`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/planning_analysis_decision.md`

## Complete minimal example (completed)

```markdown
---
type: planning_analysis_decision
status: completed
---

## Summary

- [SUM-1] The plan is executor-ready; every step has targets and verification.
```

## Complete example (request_changes)

```markdown
---
type: planning_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] The plan needs revision before execution.

## What Came Up Short

- [PA-001] Critical Files omits the real target file.

## How To Fix

- [PA-001] Add the target file to `## Critical Files` and resubmit the plan.
```

## Frontmatter

- `type` — required; `planning_analysis_decision`.
- `status` — required and closed: `completed`, `request_changes`, or `failed`.
  Any other value, including `done` or `wrong`, is a hard error. The
  diagnostic names all three accepted values; correct it and resubmit.

## Sections

- `## Summary` — required; exactly one item.
- `## What Came Up Short` — one item per gap; required (non-empty) when
  status is `request_changes` or `failed`, omitted when `completed`.
- `## How To Fix` — one concrete remediation per item; same
  required/omitted rule. Give each gap the SAME stable ID in both sections
  (e.g. `PA-001` in `## What Came Up Short` and `## How To Fix`); downstream
  phases cite that ID to prove closure, so keep IDs unique and stable.

## Hard errors vs warnings

Hard errors: missing or multiple Summary items; `request_changes`/`failed`
without non-empty What Came Up Short and How To Fix; wrong `type`;
duplicate item IDs; any grammar violation; or a `status` outside
`completed`, `request_changes`, and `failed`.
