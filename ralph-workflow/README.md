# Ralph Workflow (Python)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI for **AI agent orchestration** on your own machine.
It extends the simple Ralph loop into a **composable loop framework** for real software engineering, and the default workflow is already strong enough to start with before you customize anything.

This README is the **install + operator entrypoint**. It intentionally leaves out deeper material so the first screen stays onboarding-focused.

## Use this route

1. [START_HERE.md](../START_HERE.md)
2. [docs/README.md](../docs/README.md)
3. [docs/sphinx/index.rst](docs/sphinx/index.rst)

## Install

```bash
pipx install ralph-workflow
ralph --help
```

## Operator docs

- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [Configuration](docs/sphinx/configuration.md)
- [Reference](docs/sphinx/reference.md)
- [User stories](docs/sphinx/user-stories.md)

## Baseline capabilities

Ralph Workflow ships DuckDuckGo-backed web search, built-in `visit_url` text retrieval, a 17-skill baseline bundle installed by `ralph --init`, and docs-aware guidance that turns on when `arabold/docs-mcp-server` is reachable on `localhost:6280`. Prompts show a short setup hint when docs-mcp is absent. See [Web Search](docs/mcp/web-search.md), [Web Visit](docs/mcp/web-visit.md), and [MCP Servers](docs/mcp/mcp-servers.md).

## Deeper material

If you need the fuller manual, configuration detail, or maintainer-facing internals, go to `docs/sphinx/`.
In particular:

- `docs/sphinx/quickstart.md`
- `docs/sphinx/developer-reference.md`
- `docs/sphinx/modules.rst`

## Prompt Helper: interactive PROMPT.md authoring

Ralph Workflow includes an interactive prompt-helper mode for users who know what they want to build but do not want to hand-write a `PROMPT.md` from scratch.

Unlike the normal pipeline workflow, which runs a multi-stage build/verify/review loop, the prompt helper starts as a simple conversational intake: it asks what kind of product, feature, or change you want to build, then guides you through a review loop to refine a structured product-specification artifact. It only writes `PROMPT.md` when you decide to finish.

**Two ways to start the prompt helper:**

```bash
# Via the main ralph command
ralph --prompt-helper

# Via the dedicated ralph-prompt entrypoint (installed automatically with pip)
ralph-prompt
```

Both launch the same interactive experience. The `ralph-prompt` executable is installed automatically when you install `ralph-workflow` via pip.

The prompt helper:
- Begins with conversational intake, not a pipeline
- Organizes your input into a structured product-specification artifact
- Shows you a polished, readable draft and asks for feedback
- Lets you update, replace, continue refining, or finish
- Handles both small feature requests and large PRD-style product definitions
- Writes `PROMPT.md` only when you choose to finalize

To configure a dedicated agent for prompt-helper mode, add a `[prompt_helper]` section to `ralph-workflow.toml` with `agent = "your-agent"`. If omitted, it falls back to the first configured agent or the built-in `opencode` agent if no agents are configured.

## Verification

Use the canonical verification workflow:

```bash
make verify
```
