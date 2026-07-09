# Contributing to Ralph Workflow

The maintained contributor guide lives at
[`ralph-workflow/CONTRIBUTING.md`](ralph-workflow/CONTRIBUTING.md). It
covers the dev/stable build split, the `make verify` gate, the
Python-package layout, the policy-driven pipeline model, the typing
suppression policy, and the guardrails every PR must satisfy.

The two policies most contributors reach for first:

- [`docs/agents/verification.md`](docs/agents/verification.md) — what
  each step of `make verify` proves, and the non-circumvention rules.
- [`docs/agents/testing-guide.md`](docs/agents/testing-guide.md) — how
  to write black-box tests under the project's combined test budget.

## Required verification

The canonical gate is `make verify` run from the `ralph-workflow/`
directory. It runs the docs build, ruff, mypy --strict, the
60-second-capped pytest suite, and the audit scripts; the subprocess
E2E suite lives in a separate opt-in target (`make test-subprocess-e2e`).

Pull requests should be small, focused, and include tests for any
behavior change. By contributing, you agree your contributions are
licensed under AGPL-3.0-or-later.