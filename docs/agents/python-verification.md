# Python verification — redirect

This page redirects Python contributors to the canonical verification guide.

Ralph Workflow's Python package is verified through the `make verify` gate in
`ralph-workflow/`. The gate is the single source of truth for what "green"
means, so this page orients you rather than duplicating it.

- Start with [`verification.md`](verification.md) for the full 19-step gate:
  what `verify-drift`, `docs`, and `ralph.verify` prove, the immutable
  60-second combined test budget, and how to read each failure.
- Read [`testing-guide.md`](testing-guide.md) for the project's black-box
  testing expectations, fixture patterns, and the rules that keep the suite
  under the budget.
- See [`ralph-workflow/CONTRIBUTING.md`](../../ralph-workflow/CONTRIBUTING.md)
  for the dev build and the exact verification command to run before finishing
  a change.

If a verification failure points at a missing public-surface update, the
public-behavior-change rule in [`README.md`](README.md) requires the matching
docs, tests, and API reference changes in the same PR.
