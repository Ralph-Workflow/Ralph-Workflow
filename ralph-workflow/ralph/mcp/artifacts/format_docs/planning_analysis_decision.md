# planning_analysis_decision artifact format

You are reporting whether the plan faithfully translates the product criteria
into an executor-ready handoff, needs changes, or must be redone. Author
markdown and submit with `ralph_submit_md_artifact`
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

- [PA-001] Step: [S-2] Observation: The plan changes retry behavior but does not characterize the current behavior.
  Cost: The implementation could preserve or regress the wrong behavior because the executor has no baseline.
  Fix: Add a focused characterization step with the observed current retry behavior and the evidence that establishes it.

## How To Fix

- [PA-001] Add the characterization step and resubmit the plan.
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
  The two sections must form a one-to-one mapping with the same stable ID for
  each gap and fix; missing, extra, or mismatched IDs are rejected. For planning `request_changes` and `failed` decisions, each shortfall must include `Step: [S-n]` or `Plan-level:`. The former binds the finding to one submitted plan step; the latter explicitly records a plan-wide gap. Missing targets are rejected.

## Hard errors vs warnings

Hard errors: missing or multiple Summary items; `request_changes`/`failed`
without non-empty What Came Up Short and How To Fix; wrong `type`; duplicate
item IDs; any grammar violation; or a `status` outside `completed`,
`request_changes`, and `failed`. A `completed` decision that includes either
remediation section is a hard error. Remediation sections with missing, extra,
or mismatched IDs are also hard errors.
