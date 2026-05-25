# Documentation Map

Use this page after [README.md](../README.md) and [START_HERE.md](../START_HERE.md).
Those pages explain what Ralph Workflow is, why the simple core matters, and how to judge one honest first run.
This page is the switchboard for the next question you actually have.

Ralph Workflow works with the coding agents you already use.
Keep your existing setup and keep your keys to yourself unless you explicitly choose a direct integration path.

## Choose one route

### I want the fastest first successful run

- [Choose your first task](./first-task-guide.md)
- [First-task prompt templates](./first-task-prompt-templates.md)
- [Getting started in the manual](../ralph-workflow/docs/sphinx/getting-started.md)

### I want the maintained operator manual

- [Manual home](../ralph-workflow/docs/sphinx/index.rst)
- [Configuration](../ralph-workflow/docs/sphinx/configuration.md)
- [Reference](../ralph-workflow/docs/sphinx/reference.md)
- [User stories](../ralph-workflow/docs/sphinx/user-stories.md)

### I need the repo-root docs families mapped clearly

These repo-root docs are a **map of the surrounding documentation system**, not the main operator manual.
The maintained day-to-day Python/operator path is the Sphinx manual above.
Some repo-root families are current Python guidance, while others are historical or mixed-status reference.

- `docs/agents/` — current Python contributor and verification guidance for agents, testing, type-ignore policy, and verification workflow
- `docs/code-style/` — current Python documentation rubric and maintained style/process guidance; some older code-style pages may still reflect the retired Rust-era system
- `docs/tooling/` — mixed-status tooling notes; prefer current Python-specific guidance like `python-tooling.md`, treat Rust-only tooling pages as archival unless explicitly referenced
- `docs/performance/` — primarily archival / historical Rust-era performance material, not the maintained Python operator path

### I want product framing before I go deeper

- [AI agent orchestration CLI](./ai-agent-orchestration-cli.md)
- [Why the spec still matters](./spec-driven-ai-agent.md)
- [What unattended use should mean](./unattended-coding-agent.md)

## Keep proof secondary

Use proof-oriented pages only after you already understand the product story or the operator route.
If you need deeper evidence, the manual and linked supporting pages will take you there.

## Primary repo

Codeberg is the primary repo and source of truth:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>
