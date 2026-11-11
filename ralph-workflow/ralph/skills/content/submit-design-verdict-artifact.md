---
name: submit-design-verdict-artifact
description: Use when reporting a visual review verdict as a markdown artifact via ralph_submit_md_artifact, or when a design_verdict document was rejected for missing capture provenance, a malformed finding, or a verdict that disagrees with its findings
version: 2.2.0
---

# submit-design-verdict-artifact

Read `.agent/artifact-formats/design_verdict.md`. Report the outcome of a
visual review of a captured multimodal cell.

## Document Shape

Frontmatter: `type: design_verdict`. An active-run submission also carries
`judgement_tier` — `deterministic` for fixture-only machinery tests, or
`on-demand` for an operator-invoked vision review. An optional `status` echo
must be `pass`, `fail`, or `blocked`; the authoritative status lives in
`## Verdict`.

- `## Capture Provenance` — required. The body fields `run_id:`, `target:`,
  `before_id:`, `after_id:`, and `cell_ids:` are always required; an
  active-run submission also provides `verdict_id:`, `before_handles:`, and
  `after_handles:`. Every `capture_id` a finding cites must appear in
  `cell_ids`.
- `## Design Intent` — required; exactly one item, the verbatim text the
  agent was asked to review.
- `## Verdict` — required; exactly one item shaped `status | summary`.
- `## Findings` — required. Items shaped
  `capture_id | x,y,w,h | dimension | severity | narrative`, where severity
  is `blocker`, `major`, `minor`, or `info`.

A `pass` verdict may carry no `blocker` or `major` finding, and a `fail`
verdict needs at least one.

## Submit

1. Write one complete design_verdict document in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and `artifact_type: design_verdict`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
