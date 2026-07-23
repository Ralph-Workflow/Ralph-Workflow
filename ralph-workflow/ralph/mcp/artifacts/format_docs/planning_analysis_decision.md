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

- [W-1] Critical Files omits the real target file.

## How To Fix

- [FIX-1] Add the target file to primary_files and resubmit the plan.
```

## Frontmatter

- `type` — required; `planning_analysis_decision`.
- `status` — required; `completed`, `request_changes`, or `failed`. An
  unknown value is coerced to `completed` with a warning — never rely on
  that; pick the right status.

## Sections

- `## Summary` — required; exactly one item.
- `## What Came Up Short` — one item per gap; required (non-empty) when
  status is `request_changes` or `failed`, omitted when `completed`.
- `## How To Fix` — one concrete remediation per item; same
  required/omitted rule. Each item's stable ID (e.g. `FIX-1`) is what the
  next development result cites in `## Analysis Items Addressed`, so keep
  IDs unique and stable.

## Hard errors vs warnings

Hard errors: missing or multiple Summary items; `request_changes`/`failed`
without non-empty What Came Up Short and How To Fix; wrong `type`;
duplicate item IDs; any grammar violation. Warning: unknown `status`
coerced to `completed`.
