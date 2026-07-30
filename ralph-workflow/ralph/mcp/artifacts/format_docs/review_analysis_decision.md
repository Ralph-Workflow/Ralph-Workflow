# review_analysis_decision artifact format

You are reporting whether a submitted review was thorough and correct. Judge
the review's coverage, evidence, severity, and classifications against the code
and checks you inspect; do not use this artifact to redo or directly fix the
implementation.
The submitted review is direct evidence of what the reviewer covered, reported,
and classified. Use code, diff, and self-run checks to corroborate whether those
judgments are correct and complete. Statuses grade the submitted review, never
the implementation.
Author markdown and submit with `ralph_submit_md_artifact`
(`artifact_type: review_analysis_decision`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/review_analysis_decision.md`

## Complete minimal example (completed)

```markdown
---
type: review_analysis_decision
status: completed
---

## Summary

- [SUM-1] The submitted review covered the changed surfaces and its findings are supported.
```

## Complete example (request_changes)

```markdown
---
type: review_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] The submitted review missed a concrete error-path finding.

## What Came Up Short

- [RA-001] The review omitted the bare exception at src/handler.py:42.

## How To Fix

- [RA-001] Re-review src/handler.py, add the missed finding with supported severity and remediation, then resubmit the review.
```

## Frontmatter

- `type` — required; `review_analysis_decision`.
- `status` — required and closed: `completed`, `request_changes`, or `failed`.
  Any other value, including `done` or `wrong`, is a hard error. The
  diagnostic names all three accepted values; correct it and resubmit.

## Sections

- `## Summary` — required; exactly one item.
- `## What Came Up Short` — one item per gap; required (non-empty) when
  status is `request_changes` or `failed`, omitted when `completed`.
- `## How To Fix` — one concrete remediation per item; same
  required/omitted rule. Give each gap the SAME stable ID in both sections
  (e.g. `RA-001` in `## What Came Up Short` and `## How To Fix`); downstream
  phases cite that ID to prove closure, so keep IDs unique and stable.
  The two sections must form a one-to-one mapping with the same stable ID for
  each gap and fix; missing, extra, or mismatched IDs are rejected.

## Hard errors vs warnings

Hard errors: missing or multiple Summary items; `request_changes`/`failed`
without non-empty What Came Up Short and How To Fix; wrong `type`; duplicate
item IDs; any grammar violation; or a `status` outside `completed`,
`request_changes`, and `failed`. A `completed` decision that includes either
remediation section is a hard error. Remediation sections with missing, extra,
or mismatched IDs are also hard errors.
