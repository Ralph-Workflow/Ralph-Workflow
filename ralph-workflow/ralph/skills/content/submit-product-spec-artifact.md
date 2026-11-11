---
name: submit-product-spec-artifact
description: Use when turning a user's idea into a structured product specification as a markdown artifact via ralph_submit_md_artifact, or when a product_spec was rejected for a missing required section or a second Title or Scope item
version: 2.2.0
---

# submit-product-spec-artifact

Read `.agent/artifact-formats/product_spec.md`. Turn the user's idea into a
structured product specification, and submit it after the user approves the
draft.

## Document Shape

Frontmatter: `type: product_spec`.

Required sections: `## Title` (exactly one item), `## Scope` (exactly one
item), `## Goals`, `## Users`, and `## Success Criteria` (at least one item
each).

Optional sections: `## Constraints`, `## Product Behavior`,
`## UX UI Requirements`, `## Scope Boundaries`, and `## Open Questions` —
one item per point.

Keep the items distinct and scannable: five to ten well-organized items per
section beat thirty loosely related ones. Leave implementation details and
code structure out.

## Submit

1. Write one complete product_spec document in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and `artifact_type: product_spec`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
