# development_result artifact format

You are reporting the outcome of a development task: what you did, what
changed, and proof for every plan item and analysis item. Author markdown
and submit with `ralph_submit_md_artifact`
(`artifact_type: development_result`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/development_result.md`

## Complete minimal example

```markdown
---
type: development_result
status: completed
---

## Summary

- [SUM-1] Implemented token-expiry handling with tests.

## Files Changed

- [F-1] src/auth/refresh.py
- [F-2] tests/test_refresh.py

## Plan Items Proven

- [S-1] Updated src/auth/refresh.py; tests/test_refresh.py::test_race passes.
- [S-2] Ran make verify; exit 0.

## Analysis Items Addressed

- [FIX-1] Added the missing edge-case regression test.
```

## Frontmatter

- `type` — required; `development_result`.
- `status` — required and closed: `completed` or `partial`. Any other value,
  including `done` or `wrong`, is a hard error. The diagnostic names both
  accepted values; correct the frontmatter and resubmit.

## Sections

- `## Summary` — required; exactly one item.
- `## Files Changed` — required; one item per modified file, at least one.
- `## Plan Items Proven` — optional section, but proof policy requires one
  item per plan step. The item ID is the plan-step stable ID itself
  (`S-1`, `S-2`, … exactly as in the plan's `## Steps` section; for
  work-unit plans use your assigned `[unit-ID]` bracket ID exactly as it
  appears in the plan's `## Work Units` items). The item text is
  the proof. Never write "Step N: title" — reference by ID only.
- `## Analysis Items Addressed` — optional section; when analysis feedback
  exists, one item per prior `## How To Fix` item, using that item's
  stable ID as the item ID and your proof as the text.
- `## Next Steps` — exactly one item; required when `status: partial`.
- `## Continuation` — exactly one item containing the prior session id;
  required when `status: partial`.

## Hard errors vs warnings

Hard errors: missing Summary or Files Changed; more than one Summary,
Next Steps, or Continuation item; `partial` without Next Steps and
Continuation; duplicate item IDs; and (at proof validation) plan-item IDs
that do not exactly match a plan step ID or work-unit id, missing proofs,
or duplicates. An unrecognized `status` is also a hard error and reports
the valid `completed` / `partial` vocabulary.
