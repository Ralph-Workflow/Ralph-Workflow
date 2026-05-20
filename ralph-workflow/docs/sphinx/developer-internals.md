# Developer Internals

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This section is for contributors and maintainers working on Ralph Workflow itself. These pages explain the runtime architecture and internal contracts behind the operator-facing product.

If you only need to run Ralph Workflow, start with [Operator Reference](reference.md) instead.

## What lives here

- [MCP Architecture](mcp-architecture.md) — server lifecycle, capability gates, and upstream proxying
- [Artifacts](artifacts.md) — typed handoffs and artifact storage contracts
- [Prompts](prompts.md) — prompt template loading, rendering, and payload materialization
- [Transcript and Display Reference](transcript.md) — output event structure and rendering behavior
- [Supervising API](supervising-api.md) — trackable instance model for orchestration use cases

```{toctree}
:maxdepth: 1

mcp-architecture
artifacts
prompts
transcript
supervising-api
```

## Related pages

- [Developer Reference](developer-reference.md) — top-level developer docs index
- [Python API Reference](modules.rst) — autodoc for the `ralph.*` package
- [Release & Versioning](versioning.md) — release and publishing policy
