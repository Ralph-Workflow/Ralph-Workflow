---
name: submit-planning-analysis-decision-artifact
description: Use when reporting whether a plan is ready to execute as a markdown artifact via ralph_submit_md_artifact, or when a planning_analysis_decision was rejected for a missing criterion verdict, a missing Proposed revision, or a forbidden How To Fix section
version: 2.2.0
---

# submit-planning-analysis-decision-artifact

Read `.agent/artifact-formats/planning_analysis_decision.md`. Judge the plan
on substance, not on plan shape.

## Document Shape

Frontmatter: `type: planning_analysis_decision` and a closed `status` of
`completed`, `request_changes`, or `failed`.

- `## Summary` — required; exactly one item.
- `## Criterion Verdicts` — required and non-empty. Each item uses a unique
  `PA-###` ID and carries `Criterion:`, `Expected observation:`,
  `Observation:`, `Proposed revision:`, `Verdict:`, `Evidence:`,
  `Location:`, and `Cost:`.
- `## What Came Up Short` — required for `request_changes` and `failed`; it
  mirrors the localized non-met verdicts, each with its `Proposed revision:`.
- `## How To Fix` — not permitted; the revision lives inline.

The five criteria are coverage, truthfulness, actionability, parallel
decomposition, and execution conflicts. Every non-completed verdict and its
mirrored finding carries a concrete `Proposed revision:` naming the
proposed unit split, the files each unit owns, and its focused check. A
criterion that cannot be evaluated requires `failed` rather than completion.

## Submit

1. Write one complete planning_analysis_decision document in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and
   `artifact_type: planning_analysis_decision`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
