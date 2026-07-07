# Contributor Policy — `docs/agents/`

This directory is the **contributor-policy home** for the Ralph Workflow
project. It is referenced by `AGENTS.md` and `CONTRIBUTING.md` for the
mandatory contracts that govern how contributors work in this repository.

It is intentionally distinct from
[`ralph-workflow/docs/agents/`](../../ralph-workflow/docs/agents/README.md),
which is the **agent-authoring contracts** home for contributors who are
adding or modifying the agent subsystem inside the Python package.

## What lives here

- `verification.md` — what each `make verify` check proves and how to fix
  common failures
- `testing-guide.md` — black-box testing expectations and the 60-second
  combined test budget
- `type-ignore-policy.md` — when (and when not) `# type: ignore` is allowed
- `python-verification.md` — Python-specific verification policy
- `integration-tests.md` — how integration tests are organized
- `parallel-mode.md` (see ralph-workflow/docs/sphinx/parallel-mode.md) — parallelization expectations and audit contracts
- `workspace-trait.md` — workspace abstraction contract
- `agent-support-architecture.md` — how this repo supports Ralph Workflow
  agents
- `fabrication-guard.md` — fabrication-guard levels, mandatory protocol,
  and the absolute ban on inflating adoption/credits/stats claims

## What does NOT live here

Topics that belong in the agent-authoring tree under
`ralph-workflow/docs/agents/`:

- Adding a new agent (`adding-a-new-agent.md`,
  `quickstart-add-a-new-agent.md`)
- The agent subsystem architecture (`architecture.md`)
- The artifact submission contract (`artifact-submission-contract.md`)
- The memory lifecycle for agent-owned resources (`memory-lifecycle.md`)
- The pro contract (`pro-contract.md`)
- Timeout policy (`timeout-policy.md`)
- Watchdog architecture and spec (`watchdog-architecture.md`,
  `watchdog-spec.md`)

If you are unsure which tree a new doc belongs in, default to this tree for
**policy** and to the package tree for **contracts**.

## Role boundary

| Tree                                | Audience            | Purpose                                |
| ----------------------------------- | ------------------- | -------------------------------------- |
| `docs/agents/` (repo root)          | Repo contributor    | Mandatory policy + verification guides |
| `ralph-workflow/docs/agents/`       | Agent author        | Contracts for adding/extending agents  |

Cross-link, do not duplicate. If both trees would carry the same content, it
belongs in exactly one of them.

## Public behavior changes

Any change to public CLI behavior, configuration fields, artifact shapes, or
exit codes MUST be reflected in the same PR in:

1. the Sphinx manual (`ralph-workflow/docs/sphinx/`) so operators see the
   current contract;
2. the relevant tests, with a failing or updated black-box check that would
   catch the regression; and
3. `ralph-workflow/docs/sphinx/modules.rst` if the change affects the public
   Python API.

Do not treat docs, tests, or API reference as follow-up work. They are part of
the public-behavior change.