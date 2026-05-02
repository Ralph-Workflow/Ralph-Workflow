# Developer Internals

This section is for contributors and maintainers working on Ralph Workflow itself. These
pages explain the runtime architecture, internal contracts, and subsystem behavior behind
the operator-facing product.

If you only need to use Ralph Workflow, start with [Operator Reference](reference.md)
instead.

## What lives here

- [Configuration](configuration.md) — runtime config files, precedence, and policy knobs
- [Agents](agents.md) — agent resolution, chains, drains, and invocation flow
- [MCP Architecture](mcp-architecture.md) — server lifecycle, capability gates, and upstream proxying
- [Artifacts](artifacts.md) — typed handoffs and artifact storage contracts
- [Prompts](prompts.md) — prompt template loading, rendering, and payload materialisation
- [Transcript and Display Reference](transcript.md) — output event structure and rendering behavior

```{toctree}
:maxdepth: 1

agents
mcp-architecture
artifacts
prompts
transcript
```

## Related pages

- [Developer Reference](developer-reference.md) — top-level developer docs index
- [Python API Reference](modules.rst) — autodoc for the `ralph.*` package
- [Release & Versioning](versioning.md) — release and publishing policy
