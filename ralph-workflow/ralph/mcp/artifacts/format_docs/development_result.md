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

Proof IDs in `## Plan Items Proven` must exactly match the proof set
derived from the accepted plan: usable step IDs (`S-N`) for serial work; for
an explicit Work Units plan, every usable unit ID plus every unowned global
step ID; exactly the assigned unit ID for an isolated worker; or the single
fallback ID `plan` when extraction yields no usable references. Missing,
unknown, and duplicate plan-item IDs fail proof validation. When a plan
item's *proof text* claims the UI work, its proof must also cite a criterion
8 design verdict id and ralph://media capture handles — a requirement judged
from the item's proof text, never from the bracketed reference label, so a
relabeled reference never changes acceptance. `## Analysis Items Addressed`
IDs are checked the same way: duplicate analysis-item proof entries and
missing or unknown analysis finding IDs are hard errors, because analysis
finding IDs are validated exactly against the prior analysis's stable
finding IDs.

## Prose-plan proof example

When the accepted plan has no usable extracted step or unit IDs, replace the
step entries above with exactly one plan-level entry:

```markdown
## Plan Items Proven

- [plan] Fixed token refresh in src/auth/refresh.py and added tests/test_refresh.py; `pytest tests/test_refresh.py -q` exits 0 and proves concurrent refresh preserves token validity.
  Disposition: completed
```

Do not add invented step or unit IDs alongside this fallback. The bundled
example demonstrates a complete result for this case.

## Unplanned Work example (optional, any status)

Append a top-level `## Unplanned Work` section for work required by the
request but omitted from the plan. Tie each item to its request criterion,
the action and changed paths, a navigable anchor, and reproducible proof:

    ## Unplanned Work

    - [UW-1] Request criterion: same-key refreshes preserve token validity.
      Replaced the global lock in src/auth/refresh.py:78 with a bounded
      per-key lock; added tests/auth/test_refresh_race.py. Proof:
      pytest tests/auth/test_refresh_race.py::test_concurrent_refresh_keeps_token_valid
      exits 0 and confirms the refreshed token remains valid.
    - [UW-2] Request criterion: document the new refresh behavior.
      Updated docs/auth/refresh.md:12 to describe per-key serialization.
      Proof: make docs exits 0; the rendered refresh page describes
      same-key serialization and independent refreshes for different keys.

Complete omitted required work in the current development phase and prove
it before reporting completion. This section records that work without
inventing plan-step IDs; it does not authorize deferring request criteria.

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

## Partial and failed outcomes

`status: partial` and `status: failed` are accepted at any point, but neither is
a shortcut around work the developer can perform. Use `partial` only when
verified progress exists and remaining required work cannot be completed by
any developer action available in the current run. Examples include a
physical-world action such as unplugging a power cable, an operator-only
credential or decision, or an external system change outside the developer's
authority. Difficulty, elapsed time, an exhausted run budget, or ready work
the developer can still perform does not qualify. The `partial` decision
itself is role-aware: a coordinator who still owns independent ready
slices dispatches them in parallel rather than handing each one back as
a separate `partial`; a worker continues in-scope recovery within the
assigned unit per `shared/_no_exemption_for_failures.j2` (the worker
contract forbids dispatch, so the same "dispatch in parallel" rule does
not apply to a worker reading this format doc). After
submitting `partial`, call `declare_complete` once with `partial_reason`
naming that literal impossibility and required external action; no second
confirmation call is required. Use `failed` when no safe actionable
continuation exists under current evidence or authority.

## Sections

Most section rules below apply to `status: completed` only — a
completion claim is the one thing this artifact can fully check. With
`status: partial` or `status: failed` the document is otherwise free-form below
the frontmatter, with two exceptions that are always enforced: `## Summary`
with at least one item is required, so the reason for the outcome is never
silently omitted; and once the run's cycle timebox or development timebox
has warned, `## Incomplete
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
- `## Plan Items Proven` — required on a `completed` result once the
  run's cycle timebox or development timebox has warned, and proof policy
  requires one item per usable extracted plan reference whenever the result
  claims completion. Use canonical step IDs when usable steps exist,
  including serial execution of a Work Units plan. Alternatively, for an
  explicit Work Units plan, prove every usable unit ID plus every unowned
  global step ID (including integration steps). Unit proof covers its owned
  steps; do not additionally submit their step IDs. An isolated worker
  proves exactly its assigned unit ID. When extraction yields no usable IDs,
  provide exactly one item with ID `plan` to prove the accepted prose plan.
  A completed item whose proof text claims the UI work must also cite a
  design verdict id and capture handles, judged from the item's proof
  text, never from the bracketed reference label.
  The item text is the proof. Add an indented `Disposition:` field with
  one of `completed`, `adapted`, `not_applicable`, or `blocked`. Add an
  indented `Rationale:` for `adapted`, `not_applicable`, and
  `blocked`. A completed artifact cannot contain `blocked`; necessary
  blocked work requires `status: partial`.
- `## Analysis Items Addressed` — optional section; when analysis feedback
  exists, one item per prior `## What Came Up Short` finding, using that
  finding's stable ID as the item ID and proof of closure as the text.
- `## Next Steps` — optional; exactly one item.
- `## Continuation` — optional; exactly one item containing the prior
  session id.
- `## Unplanned Work` — optional bulleted section, accepted at any
  status, for required work the plan did not name. One
  `- [UW-N]` bullet per item, naming its request criterion, action,
  changed paths, anchor, and reproducible proof. The anchor is a
  stable `path:line` (or `path:line-line`) location with a reproducible
  piece of evidence. The bracketed IDs in this section are anchors,
  not plan-step references, and are not routed to proof validation —
  they never substitute for `## Plan Items Proven` or
  `## Analysis Items Addressed`. Finish omitted required work now;
  the optional section does not relax completion requirements or
  replace proof for any existing plan or analysis item.

## Hard errors vs warnings

Hard errors at any status: an unrecognized `status`; a missing `## Summary`;
and, once the cycle timebox or the development timebox has warned, a missing
or malformed `## Incomplete Work` on a `partial`/`failed` result or a
missing `## Plan Items Proven` on a `completed` one. Whether the timebox
warned is read from the run's own published clock for the relevant timer
(cycle or development), with a matching declared frontmatter flag —
`cycle_timebox_warned: true` or `development_timebox_warned: true` — also
honoured, so a result validated outside the warned invocation (a replay
or a hand-written report) keeps its stricter reading.

Hard errors for `status: completed` only: missing Summary
or Files Changed; more than one Summary, Next Steps, or Continuation
item; duplicate item IDs; a missing or unknown `Disposition`; a missing
`Rationale` for `adapted`, `not_applicable`, or `blocked`; `blocked` in a
completed result; and (at proof validation) plan-item IDs that do not
exactly match a usable plan step ID, work-unit ID, or the `plan` fallback
when no IDs are usable, missing plan-item proofs, duplicate analysis-item
proof entries, and missing or unknown analysis finding IDs — analysis
finding IDs are validated exactly against the prior analysis's stable
finding IDs. A completed plan item whose proof text claims the UI work
also requires a design verdict id and capture handles, judged from the
proof text, never from the reference label. The unrecognized-`status`
error reports the valid `completed` / `partial` / `failed` vocabulary.
