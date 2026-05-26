# Ralph Workflow (Python)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI for **AI agent orchestration** on your own machine.
It extends the simple Ralph loop into a **composable loop framework** for real software engineering, and the default workflow is already strong enough to start with before you customize anything.

This README is the **install + operator entrypoint**, not the main product pitch.
It intentionally leaves out deeper material that belongs in the manual and developer docs.

## Use this route

1. [START_HERE.md](../START_HERE.md)
2. [docs/README.md](../docs/README.md)
3. [docs/sphinx/index.rst](docs/sphinx/index.rst)

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

- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [Configuration](docs/sphinx/configuration.md)
- [Reference](docs/sphinx/reference.md)
- [User stories](docs/sphinx/user-stories.md)

## Baseline capabilities

Ralph Workflow ships a built-in local work surface for workspace and file operations, git read/status/diff/log operations, artifact submission, plan-reading, and media-read support. `ralph --init` sets up or repairs the baseline capability bundle so the default workflow is ready on first run and can recover from degraded helpers. `ralph --diagnose` reports capability health before you start work so you can spot missing, unreachable, degraded, or outdated pieces early.

It also ships DuckDuckGo-backed web search, built-in `visit_url` single-page retrieval with SSRF-safe defaults, a 17-skill baseline bundle installed by `ralph --init`, and docs-aware guidance that turns on when `arabold/docs-mcp-server` is reachable on `localhost:6280`. When docs-mcp is absent, prompts show a short setup hint instead of pretending docs lookup is available. See [Web Search](docs/mcp/web-search.md), [Web Visit](docs/mcp/web-visit.md), and [MCP Servers](docs/mcp/mcp-servers.md).

## Deeper material

- [Developer Reference](docs/sphinx/developer-reference.md)
- [Modules index](docs/sphinx/modules.rst)
