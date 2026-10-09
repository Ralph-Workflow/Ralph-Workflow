# development_analysis_decision artifact format

Report whether each criterion fixed by the request and plan is met. Submit
markdown with `ralph_submit_md_artifact`
(`artifact_type: development_analysis_decision`).

## Free-form body

The frontmatter `status` is the only field the validator mechanically
checks; routing reads it. Below the frontmatter, write the decision in
your own words. The body is the next agent's reading matter: a useful
shape describes what fell short, the evidence that supports the
judgement, and (for `request_changes`) the leftover work the next
agent should pick up. There is no required section, no required field
label, and no required stable ID for individual items.

`status` is `completed`, `request_changes`, or `failed`. The three
meanings are:

- `completed` — every unchanged request criterion is met, and no
  necessary plan work remains.
- `request_changes` — localized unmet work is actionable in this
  development cycle. The body describes what fell short, what
  evidence supports it, and what the next agent should do. Split the
  remaining work into independent units and dispatch them in parallel
  rather than handing each one back as a separate `partial` decision.
- `failed` — a criterion is impossible, contradictory, or not
  evaluable, or necessary work has no safe actionable route. `failed`
  records stronger or not-evaluable evidence for explicit resolution;
  the label itself never fails the pipeline.

Bundled policy routes both `request_changes` and `failed` back to
development. A `failed` decision adds an explicit mandate to
determine whether and how the failure can be resolved, without
treating the label itself as a pipeline failure. Terminal failure
remains reserved for real pipeline or recovery exhaustion.

## Whole-change review

The body is the right place for the whole-change review that
development analysis owns: the parallel pieces fit together, nothing
outside the plan regressed, no unrelated scope, and the change follows
repository policy. These are judgment criteria, not validated fields.

## Example shape

```markdown
---
type: development_analysis_decision
status: request_changes
---

## Summary
- [SUM-1] One criterion is not met.

The focused regression test for oversized indexes is missing; the
rest of the work is sound. A developer cycle should add a
parametrized oversized-index case to `tests/test_foo.py` and re-run
the focused tests.

`pytest tests/test_foo.py -q` shows 18 passed but no oversized-index
case. The remaining work splits into one independent unit that a
worker can pick up: WU-X — add the parametrized oversized-index test
case at `tests/test_foo.py`; check is `pytest tests/test_foo.py -q
-k oversized` exits 0 with at least one new oversized case.
```

The body is the next agent's reading matter; the validator only
checks the frontmatter `status` enum. The shape above is a useful
example, not a required form. For a `request_changes` decision the
useful shape is a planner-style fix plan: name the gap, cite the
evidence, and split the remaining work into independent units with
ownership and a per-unit check, then dispatch the units in parallel.

See `.agent/artifact-formats/examples/development_analysis_decision.md`
for the validator-backed complete example.
