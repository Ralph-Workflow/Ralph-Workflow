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
  Disposition: completed
- [S-2] Ran make verify; exit 0.
  Disposition: completed

## Analysis Items Addressed

- [DA-001] Added the missing edge-case regression test.
```

## Adapted and not-applicable examples

```markdown
---
type: development_result
status: completed
---

## Summary

- [SUM-1] Reconciled inaccurate plan premises without weakening the request.

## Files Changed

- [F-1] src/serialization.py

## Plan Items Proven

- [S-3] Used the repository's existing serializer after the planned module was absent; `pytest tests/test_serialization.py -q` passes.
  Disposition: adapted
  Rationale: `src/planned_serializer.py` does not exist, while `src/serialization.py` owns the same request outcome and the focused regression test proves it.
- [S-4] No migration was needed because the target schema already contains the requested indexed field at `db/schema.sql:42` and `pytest tests/test_schema.py -q` passes.
  Disposition: not_applicable
  Rationale: The plan assumed the indexed field was absent; the cited schema and focused check contradict that premise while preserving the request criterion.
```

Elapsed time, difficulty, an unrelated passing check, or an unsupported claim
that a step is unnecessary are invalid rationales for `not_applicable`.

## Frontmatter

- `type` — required; `development_result`.
- `status` — required and closed: `completed`, `partial`, or `failed`. Any other value,
  including `done` or `wrong`, is a hard error. The diagnostic names all
  accepted values; correct the frontmatter and resubmit.

## Sections

Most section rules below apply to `status: completed` only — a
completion claim is the one thing this artifact can fully check. With
`status: partial` or `status: failed` the document is otherwise free-form below
the frontmatter, with two exceptions that are always enforced: `## Summary`
with at least one item is required, so the reason for the outcome is never
silently omitted; and once the run's cycle timebox has warned, `## Incomplete
Work` is required, with a stable-ID bracket, a `Reason:` field, and an
`Evidence:` field on every item. The `## Incomplete Work` section is a CLOSED grammar, not free-form: it accepts only top-level `- [ID] text` bullets and their indented `Reason:` / `Evidence:` lines, in a single section. Prose, other bullet markers, numbered lists, nested entries, extra fields, `### [ID]` sub-blocks and a repeated section are all rejected — not because they are wrong to write, but because the report reads none of them, so accepting them would silently delete the work they describe. Put every remaining item in its own stable-ID bullet.

Under that same warning a `completed` result
must carry `## Plan Items Proven` — the status you choose does not decide
whether you are asked to show your work. Still lead with
what you did and what remains. For `partial`, include `## Next Steps` and your
session id in `## Continuation` so a safe concrete continuation can resume. Use
`failed` when no safe developer continuation exists under current evidence or
authority; report the blocker without promising another iteration. Neither
status decides whether the run ends.

- `## Summary` — required; exactly one item.
- `## Files Changed` — required; one item per modified file, at least one.
- `## Plan Items Proven` — optional section, but proof policy requires one
  item per plan step. The item ID is the plan-step stable ID itself
  (`S-1`, `S-2`, … exactly as in the plan's `## Steps` section; for
  work-unit plans use your assigned `[unit-ID]` bracket ID exactly as it
  appears in the plan's `## Work Units` items). The item text is
  the proof. Never write "Step N: title" — reference by ID only.
  Add an indented `Disposition:` field with one of `completed`, `adapted`,
  `not_applicable`, or `blocked`. Add an indented `Rationale:` for
  `adapted`, `not_applicable`, and `blocked`. A completed artifact cannot
  contain `blocked`; necessary blocked work requires `status: partial`.
- `## Analysis Items Addressed` — optional section; when analysis feedback
  exists, one item per prior `## What Came Up Short` finding, using that
  finding's stable ID as the item ID and proof of closure as the text.
- `## Next Steps` — optional; exactly one item.
- `## Continuation` — optional; exactly one item containing the prior
  session id.

## Hard errors vs warnings

Hard errors at any status: an unrecognized `status`; a missing `## Summary`;
and, once the cycle timebox has warned, a missing or malformed `## Incomplete
Work` on a `partial`/`failed` result or a missing `## Plan Items Proven` on a
`completed` one. Whether the cycle warned is read from the run's own clock, not
from anything the document declares.

Hard errors for `status: completed` only: missing Summary
or Files Changed; more than one Summary, Next Steps, or Continuation
item; duplicate item IDs; a missing or unknown `Disposition`; a missing
`Rationale` for `adapted`, `not_applicable`, or `blocked`; `blocked` in a
completed result; and (at proof validation) plan-item IDs that
do not exactly match a plan step ID or work-unit id, missing proofs, or
duplicates. The unrecognized-`status` error reports the valid
`completed` / `partial` / `failed` vocabulary.
