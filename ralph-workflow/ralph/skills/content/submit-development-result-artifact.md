---
name: submit-development-result-artifact
description: Use when submitting a development_result artifact as markdown via ralph_submit_md_artifact with ID-based proof entries in Plan Items Proven and Analysis Items Addressed, or when a completed result was rejected for a missing section or an unproven plan or analysis item
version: 2.1.0
---

# submit-development-result-artifact

## Overview

A development result is one markdown document
(`artifact_type: "development_result"`) reporting what was done, which
files changed, and — as stable-ID list items — the proof that plan steps
and analysis findings were actually addressed.

Submit with `ralph_submit_md_artifact`; pre-check with
`ralph_verify_md_artifact`.

## Document Shape

Frontmatter: `type: development_result` and exactly one closed-vocabulary
status: `completed` or `partial`. Any other status is invalid and must be
repaired before submission.

The section rules below apply to `status: completed` only. A
`status: partial` document is free-form below the frontmatter: no
section is required or shaped, and no proof is demanded. Write whatever
best hands your unfinished work to the next iteration — `## Summary`,
`## Next Steps` and `## Continuation` (your session ID) are read when
present and are the most useful things to include.

| Section | Required (`completed`) | Items |
|---|---|---|
| `## Summary` | yes | exactly 1 |
| `## Files Changed` | yes | 1+ (one file per item) |
| `## Plan Items Proven` | no | one per plan step proven |
| `## Next Steps` | no | exactly 1 |
| `## Continuation` | no | exactly 1: the prior session ID |
| `## Analysis Items Addressed` | no | one per analysis finding addressed |

## ID-Based Proof References

Proof entries reference other artifacts by their stable item IDs — the ID
goes in the `[ID]` slot and the proof is the item text:

- `## Plan Items Proven`: the item ID is the plan step's stable ID
  (`S-1`, `S-2`, …) exactly as it appears in the plan's `## Steps`
  section. The text states the concrete evidence that the step is done.
- `## Analysis Items Addressed`: the item ID is the stable ID of the
  `## What Came Up Short` finding in the analysis-decision artifact you are
  answering. The text states concrete evidence that the finding is closed.

Copy the IDs from the source artifact — do not invent or renumber them.

## Core Flow

1. Write the document. For `completed`, every section rule and every
   plan/analysis proof above is enforced. For `partial`, nothing below
   the frontmatter is enforced — still lead with what you did, what
   remains (`## Next Steps`) and your session ID (`## Continuation`) so
   the next iteration can resume.
2. Optionally `ralph_verify_md_artifact`, then
   `ralph_submit_md_artifact({"artifact_type": "development_result", "content": ...})`.

Worked example:

```markdown
---
type: development_result
status: completed
---

## Summary

- [SUM-1] Added the foo() regression test, clamped the index in src/foo.py, and verified the focused suite passes.

## Files Changed

- [F-1] src/foo.py
- [F-2] tests/test_foo.py

## Plan Items Proven

- [S-1] tests/test_foo.py contains test_clamp_handles_out_of_range_index.
- [S-2] src/foo.py clamps the index before lookup while preserving the public foo() signature.

## Analysis Items Addressed

- [DA-001] pytest tests/test_foo.py -q passes with the new regression test included.
```

## Error Recovery

- `completed development_result artifacts require summary` (or
  `... require files_changed`) — fill the missing section, or change
  `status` to `partial` if the work is not actually done.
- `Summary must contain exactly one item` / `Next Steps must contain
  exactly one item` / `Continuation must contain exactly one item` —
  these sections are single-item; merge extra items into one line.
- `section requires list items` on `## Files Changed` — list at least one
  changed file.
- Duplicate-ID diagnostics in a proof section — each plan step or finding
  may appear only once; merge the proof text into one item.
