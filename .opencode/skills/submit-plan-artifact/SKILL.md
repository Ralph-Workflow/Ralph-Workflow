---
name: submit-plan-artifact
description: Use when authoring or revising a markdown plan artifact
version: 2.2.0
---

# submit-plan-artifact

Read `.agent/artifact-formats/plan.md`. Ground the plan in repository evidence:
cover the outcome, current behavior, concrete change, risks, and runnable proof.

Parallel work is the default. Split independent responsibilities into `## Work
Units` when that helps the executor. A unit can name `Directories:` and/or
exact `Paths:`; use dependencies only for real prerequisites, put shared
contracts before consumers, and include integration after fan-in. A wholly
linear plan states its real coupling.

## Submit

1. Write one complete plan in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and `artifact_type: plan`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.

Submission checks only readable, non-empty text with at least ten words,
no more than 4,000,000 raw UTF-8 bytes, and recognizable plan intent. It does not enforce headings,
IDs, fields, graphs, ownership, or a worker/unit cap. Extraction is best effort,
so useful prose is never replaced by partial structure.
