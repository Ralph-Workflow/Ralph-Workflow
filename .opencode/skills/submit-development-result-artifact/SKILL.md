---
name: submit-development-result-artifact
description: Use when submitting a development_result artifact as markdown via ralph_submit_md_artifact with ID-based proof entries in Plan Items Proven and Analysis Items Addressed, or when a partial result was rejected for missing Next Steps or Continuation
version: 2.0.0
---

# submit-development-result-artifact

## Overview

A development result is one markdown document
(`artifact_type: "development_result"`) reporting what was done, which
files changed, and — as stable-ID list items — the proof that plan steps
and analysis fixes were actually addressed.

Submit with `ralph_submit_md_artifact`; pre-check with
`ralph_verify_md_artifact`.

## Document Shape

Frontmatter: `type: development_result` and `status: completed` or
`status: partial` (an unknown status is coerced to `completed` with a
warning — set it deliberately).

| Section | Required | Items |
|---|---|---|
| `## Summary` | yes | exactly 1 |
| `## Files Changed` | yes | 1+ (one file per item) |
| `## Plan Items Proven` | no | one per plan step proven |
| `## Next Steps` | only for `partial` | exactly 1 |
| `## Continuation` | only for `partial` | exactly 1: the prior session ID |
| `## Analysis Items Addressed` | no | one per analysis fix addressed |

## ID-Based Proof References

Proof entries reference other artifacts by their stable item IDs — the ID
goes in the `[ID]` slot and the proof is the item text:

- `## Plan Items Proven`: the item ID is the plan step's stable ID
  (`S-1`, `S-2`, …) exactly as it appears in the plan's `## Steps`
  section. The text states the concrete evidence that the step is done.
- `## Analysis Items Addressed`: the item ID is the stable ID of the
  `## How To Fix` item in the analysis-decision artifact you are
  answering. The text states the concrete evidence for that fix.

Copy the IDs from the source artifact — do not invent or renumber them.

## Core Flow

1. Write the document. For `partial`, include both `## Next Steps` and
   `## Continuation` — the canonical validator rejects a partial result
   without them.
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

- [FIX-1] pytest tests/test_foo.py -q passes with the new regression test included.
```

## Error Recovery

- `partial development_result artifacts require next_steps` (or
  `... require continuation`) — add the missing single-item section, or
  change `status` to `completed` if the work is truly done.
- `Summary must contain exactly one item` / `Next Steps must contain
  exactly one item` / `Continuation must contain exactly one item` —
  these sections are single-item; merge extra items into one line.
- `section requires list items` on `## Files Changed` — list at least one
  changed file.
- Duplicate-ID diagnostics in a proof section — each plan step or fix
  item may appear only once; merge the proof text into one item.
