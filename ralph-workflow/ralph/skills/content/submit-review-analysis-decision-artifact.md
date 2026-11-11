---
name: submit-review-analysis-decision-artifact
description: Use when reporting whether a submitted review was thorough and correct as a markdown artifact via ralph_submit_md_artifact, or when a review_analysis_decision was rejected for a malformed status or a mismatched remediation section
version: 2.2.0
---

# submit-review-analysis-decision-artifact

Read `.agent/artifact-formats/review_analysis_decision.md`. Grade the
submitted review, never the implementation.

## Document Shape

Frontmatter: `type: review_analysis_decision` and a closed `status` of
`completed`, `request_changes`, or `failed`.

- `## Summary` — required; exactly one item.
- `## What Came Up Short` — one item per gap; required (non-empty) when the
  status is `request_changes` or `failed`, and omitted when it is
  `completed`.
- `## How To Fix` — one concrete remediation per item, with the same
  required/omitted rule as `## What Came Up Short`.

Give each gap the SAME stable ID in both remediation sections; the two
sections form a one-to-one mapping, and missing, extra, or mismatched IDs
are rejected. A `completed` decision carries neither remediation section.

## Submit

1. Write one complete review_analysis_decision document in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and
   `artifact_type: review_analysis_decision`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
