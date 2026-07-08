# Contributor Policy — `docs/agents/`

This directory is the **contributor-policy home** for the Ralph Workflow
project. It is referenced by `AGENTS.md` and `CONTRIBUTING.md` for the
mandatory contracts that govern how contributors work in this repository.

It is intentionally distinct from
[`ralph-workflow/docs/agents/`](../../ralph-workflow/docs/agents/README.md),
which is the **agent-authoring contracts** home for contributors who are
adding or modifying the agent subsystem inside the Python package.

## What lives here

- `verification.md` — what each `make verify` check proves
- `testing-guide.md` — black-box testing expectations and the 60-second
  combined test budget
- `type-ignore-policy.md` — when `# type: ignore` is allowed
- `workspace-trait.md` — workspace abstraction contract
- `agent-support-architecture.md` — how this repo supports Ralph
  Workflow agents
- `fabrication-guard.md` — fabrication-guard levels and the absolute
  ban on inflating adoption/credits/stats claims

## Role boundary

| Tree                                | Audience         | Purpose                                |
| ----------------------------------- | ---------------- | -------------------------------------- |
| `docs/agents/` (repo root)          | Repo contributor | Mandatory policy + verification guides |
| `ralph-workflow/docs/agents/`       | Agent author     | Contracts for adding/extending agents  |

Cross-link, do not duplicate. Public behavior changes MUST land in the
Sphinx manual and the relevant tests in the same PR.
