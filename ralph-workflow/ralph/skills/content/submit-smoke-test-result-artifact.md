---
name: submit-smoke-test-result-artifact
description: Use when reporting the outcome of a manual runtime smoke test as a markdown artifact via ralph_submit_md_artifact, or when a smoke_test_result was rejected for a malformed status, a missing output_file, or empty Headless Guide Checks
version: 2.2.0
---

# submit-smoke-test-result-artifact

Read `.agent/artifact-formats/smoke_test_result.md`. Report what was
observed to work and what broke.

## Document Shape

Frontmatter: `type: smoke_test_result`, a closed `status` of `passed`,
`failed`, or `partial`, and `output_file` pointing at the smoke output file
(keep it under `tmp/`).

- `## Summary` — required; exactly one item.
- `## Observed Working` — optional; one item per signal actually observed.
- `## Observed Breaks` — one item per concrete break; required (non-empty)
  when the status is `failed`.
- `## Headless Guide Checks` — required; at least one item naming each
  semantic check compared against the headless contract.

Record only what was actually observed; never guess behavior.

## Submit

1. Write one complete smoke_test_result document in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and
   `artifact_type: smoke_test_result`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
