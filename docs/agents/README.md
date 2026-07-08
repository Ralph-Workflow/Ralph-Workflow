# Contributor Policy — `docs/agents/`

This directory holds the **contributor-policy** home for the Ralph
Workflow project. It is referenced by
[`AGENTS.md`](../../AGENTS.md) and
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the mandatory contracts
that govern how contributors work in this repository.

It is intentionally distinct from
[`ralph-workflow/docs/agents/`](../../ralph-workflow/docs/agents/README.md),
which is the **agent-authoring contracts** home for contributors who
are adding or modifying the agent subsystem inside the Python package.
Cross-link, do not duplicate.

## Pages

- `verification.md` — what each `make verify` step proves
- `testing-guide.md` — black-box testing expectations and the
  combined test budget
- `type-ignore-policy.md` — when `# type: ignore` is allowed
- `workspace-trait.md` — workspace abstraction contract
- `agent-support-architecture.md` — how this repo supports Ralph
  Workflow agents
- `fabrication-guard.md` — fabrication-guard levels and the absolute
  ban on inflating adoption / credits / stats claims