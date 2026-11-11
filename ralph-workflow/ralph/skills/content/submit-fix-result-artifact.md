---
name: submit-fix-result-artifact
description: Use when reporting the outcome of a fix task as a markdown artifact via ralph_submit_md_artifact, or when a fix_result document was rejected for a missing Summary or Files Changed section
version: 2.2.0
---

# submit-fix-result-artifact

Read `.agent/artifact-formats/fix_result.md`. Report what you fixed and
which files changed.

## Document Shape

Frontmatter: `type: fix_result`. There is no status field.

- `## Summary` — required; exactly one item describing what was fixed.
- `## Files Changed` — required; at least one item, one per modified file.
- `## Next Steps` — optional; at most one item for a remaining follow-up.

Unknown descriptive frontmatter fields and sections are accepted and ignored;
the known fields and sections keep their required item counts.

## Submit

1. Write one complete fix_result document in Markdown.
2. Optionally preview it with `ralph_verify_md_artifact`.
3. Submit with `ralph_submit_md_artifact` and `artifact_type: fix_result`.
4. For a revision use `ralph_edit_md_artifact`; use staged-draft tools only
   when assembling a replacement. Use `ralph_discard_md_draft` only for a
   genuine restart.
5. After a receipt, call `declare_complete` as the final action.
