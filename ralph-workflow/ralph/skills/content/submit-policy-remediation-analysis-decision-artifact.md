---
name: submit-policy-remediation-analysis-decision-artifact
description: Use when reporting whether every declared policy fact, command, marker, and script holds up as a markdown artifact via ralph_submit_md_artifact, or when a policy_remediation_analysis_decision was rejected for a missing criterion verdict or an unmatched shortfall
version: 2.2.0
---

# submit-policy-remediation-analysis-decision-artifact

Read `.agent/artifact-formats/policy_remediation_analysis_decision.md`.
Probe every declared policy fact, command, marker, and script, then report
whether each one holds up.

## Document Shape

Frontmatter: `type: policy_remediation_analysis_decision` and a closed
`status` of `completed`, `request_changes`, or `failed`.

- `## Summary` — required; exactly one item.
- `## Criterion Verdicts` — required and non-empty for every decision. Each
  item has a unique `PR-###` ID plus `Criterion:`, `Expected observation:`,
  `Verdict:`, a non-empty `Evidence:`, and a non-empty `Location:`.
- `## What Came Up Short` — required and non-empty for `request_changes` and
  `failed`; it mirrors the attributable non-met verdicts under the same ID
  and is omitted for `completed`.
- `## How To Fix` — not permitted.

`met` means no counterexample was found. A criterion that cannot be
evaluated requires `failed` rather than completion. A gate that exposes a
product failure is attribution, not a policy shortfall.

## Submit

1. Write one complete policy_remediation_analysis_decision document in
   Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and
   `artifact_type: policy_remediation_analysis_decision`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
