# Ralph Workflow (Python)

**⭐ Star on Codeberg:** [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — _primary repo_
**GitHub mirror:** [github.com/Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI for **AI agent orchestration** on your own machine.
It extends the simple Ralph loop into a **composable loop framework** for real software engineering, and the default workflow is already strong enough to start with before you customize anything.

This README is the **install + operator entrypoint**, not the main product pitch.
The full docs, first-task guide, and developer material live on [ralphworkflow.com](https://ralphworkflow.com/docs).

## Install

```bash
pipx install ralph-workflow
ralph --help
```

## Verification

When you change Ralph Workflow itself, the canonical repo-level verification command is:

```bash
make verify
```

## Operator docs

- [Getting Started](https://ralphworkflow.com/docs/getting-started.html)
- [Quickstart](https://ralphworkflow.com/docs/quickstart.html)
- [Configuration](https://ralphworkflow.com/docs/configuration.html)
- [Reference](https://ralphworkflow.com/docs/reference.html)
- [User stories](https://ralphworkflow.com/docs/user-stories.html)

## Baseline capabilities

Ralph Workflow ships a built-in local work surface for workspace and file operations, git read/status/diff/log operations, artifact submission, plan-reading, and media-read support. `ralph --init` sets up or repairs the baseline capability bundle so the default workflow is ready on first run and can recover from degraded helpers. `ralph --diagnose` reports capability health before you start work so you can spot missing, unreachable, degraded, or outdated pieces early.

It also ships DuckDuckGo-backed web search, built-in `visit_url` single-page retrieval with SSRF-safe defaults, mirrored upstream skill bundles installed by `ralph --init`, and docs-aware guidance that turns on when `arabold/docs-mcp-server` is reachable on `localhost:6280`. When docs-mcp is absent, prompts show a short setup hint instead of pretending docs lookup is available. See [Web Search](https://ralphworkflow.com/docs/mcp/web-search.html), [Web Visit](https://ralphworkflow.com/docs/mcp/web-visit.html), and [MCP Servers](https://ralphworkflow.com/docs/mcp/mcp-servers.html).

## Deeper material

- [Developer Reference](https://ralphworkflow.com/docs/developer-reference.html)
- [Modules index](https://ralphworkflow.com/docs/modules/index.html)
