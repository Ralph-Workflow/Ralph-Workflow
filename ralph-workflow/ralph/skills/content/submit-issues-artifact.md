---
name: submit-issues-artifact
description: Use when reporting issues found in a review as a markdown artifact via ralph_submit_md_artifact, or when an issues document was rejected for a malformed status, a missing remediation section, or a wrong item shape
version: 2.2.0
---

# submit-issues-artifact

Read `.agent/artifact-formats/issues.md`. Report what is wrong, where it
is, and how to fix it.

## Document Shape

Frontmatter: `type: issues` and a closed `status` of `issues_found` or
`no_issues`.

- `## Summary` — required; exactly one item.
- `## Review Evidence` — one item per checked dimension or requirement,
  shaped `dimension or requirement | concrete evidence`.
- `## Issues` — items shaped `path | severity | summary`.
- `## What Came Up Short` — one item per gap.
- `## How To Fix` — one item per concrete remediation step.

With `status: issues_found`, `## Issues`, `## What Came Up Short`, and
`## How To Fix` must all be present and non-empty. With `status: no_issues`,
omit those three sections and give concrete `## Review Evidence` instead of
an unsupported clean claim.

## Submit

1. Write one complete issues document in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and `artifact_type: issues`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
