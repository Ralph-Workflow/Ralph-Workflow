# development_analysis_decision artifact format

You are reporting the outcome of a development analysis review: whether the
implementation is acceptable, needs changes, or must be redone. Author
markdown and submit with `ralph_submit_md_artifact`
(`artifact_type: development_analysis_decision`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/development_analysis_decision.md`

## Complete minimal example (completed)

```markdown
---
type: development_analysis_decision
status: completed
---

## Summary

- [SUM-1] Implementation matches the plan and all verification passes.
```

## Complete example (request_changes)

```markdown
---
type: development_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] The implementation still needs revision.

## What Came Up Short

- [DA-001] The verification strategy was not executed for the parser change.

## How To Fix

- [DA-001] Run the exact pytest target for the parser and record the output.
```

## Frontmatter

- `type` — required; `development_analysis_decision`.
- `status` — required and closed: `completed`, `request_changes`, or `failed`.
  Any other value, including `done` or `wrong`, is a hard error. The
  diagnostic names all three accepted values; correct it and resubmit.

## Sections

- `## Summary` — required; exactly one item.
- `## What Came Up Short` — one item per gap; required (non-empty) when
  status is `request_changes` or `failed`, omitted when `completed`.
- `## How To Fix` — one concrete remediation per item; same
  required/omitted rule. Give each gap the SAME stable ID in both sections
  (e.g. `DA-001` in `## What Came Up Short` and `## How To Fix`); that ID is
  what the next development result cites in `## Analysis Items Addressed`,
  so keep IDs unique and stable.

## Hard errors vs warnings

Hard errors: missing or multiple Summary items; `request_changes`/`failed`
without non-empty What Came Up Short and How To Fix; wrong `type`;
duplicate item IDs; any grammar violation; or a `status` outside
`completed`, `request_changes`, and `failed`.
