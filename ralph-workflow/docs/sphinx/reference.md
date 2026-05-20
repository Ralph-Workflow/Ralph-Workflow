# Operator Reference

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This section is for people running Ralph Workflow in real projects.

Use it when you need commands, config, tool behavior, or web-access details. If you need runtime internals or Python implementation details, use [Developer Reference](developer-reference.md) instead.

## What lives here

- [CLI Reference](cli.md) — commands, flags, and sub-commands
- [Configuration Reference](configuration.md) — config files, precedence, and common workflow knobs
- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) — workflow graph, phases, counters, recovery, fan-out
- [Advanced Artifact Configuration](advanced-artifact-configuration.md) — artifact contracts, decision vocabularies, summaries
- [Advanced MCP Configuration](advanced-mcp-configuration.md) — MCP servers, search, crawl, and web integrations
- [End-User Stories](user-stories.md) — common user goals and the shortest next doc for each one
- [MCP Tools](mcp-tools.md) — the built-in tool surface exposed to agents
- [Local Web Access](local-web-access.md) — search, visit, and crawl behavior

## Common operator questions

- **I need to edit `ralph-workflow.toml`** → [Configuration Reference](configuration.md)
- **I do not know whether to edit global config, local config, or pipeline policy** → [Configuration Reference](configuration.md)
- **I need advanced docs for `pipeline.toml`** → [Advanced Pipeline Configuration](advanced-pipeline-configuration.md)
- **I need advanced docs for `artifacts.toml`** → [Advanced Artifact Configuration](advanced-artifact-configuration.md)
- **I need advanced docs for `mcp.toml`** → [Advanced MCP Configuration](advanced-mcp-configuration.md)
- **I want the shortest docs path for my use case** → [End-User Stories](user-stories.md)

```{toctree}
:maxdepth: 1

cli
configuration
advanced-pipeline-configuration
advanced-artifact-configuration
advanced-mcp-configuration
user-stories
mcp-tools
local-web-access
```

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough
- [Concepts](concepts.md) — the small set of terms you need most often
- [Developer Reference](developer-reference.md) — maintainer and API docs
