---
type: development_analysis_decision
status: request_changes
---

The focused regression test for oversized indexes is missing; the
rest of the work is sound. `pytest tests/test_foo.py -q` reports
18 passed but contains no oversized-index case. A developer cycle
should add a parametrized oversized-index case and re-run the
focused tests.

The remaining work splits into three independent units that the
next agent should dispatch in parallel, rather than handing each
unit back as a separate `partial`:

- WU-1 — owner `tests/test_foo.py`. Add a parametrized
  oversized-index case there. Unit check:
  `pytest tests/test_foo.py -q -k oversized` exits 0 with at
  least one new oversized case.
- WU-2 — owner `src/index.py`. Cap the index lookup at the
  configured maximum so a pathologically large index no longer
  triggers the regression. Unit check:
  `pytest tests/test_index_cap.py -q` exits 0.
- WU-3 — owner `docs/index-limits.md`. Document the new cap and
  the operator-facing error message. Unit check: the doc renders
  without a Sphinx warning in the bounded docs build.

The body is free-form; the validator only checks the frontmatter
`status` enum. The fix-plan shape above is the useful target for
a `request_changes` decision, not a required form.
