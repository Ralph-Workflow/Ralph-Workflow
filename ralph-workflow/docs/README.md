# Documentation Map (package-side router)

See the canonical product positioning in [README.md](../README.md).

> **Codeberg is primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

This page routes package-side readers to the maintained operator manual.
Use it after [`../README.md`](../README.md) and
[`../START_HERE.md`](../START_HERE.md).

## Operator route

Maintained manual flow: [Manual home](sphinx/index.rst) →
[Getting Started](sphinx/getting-started.md) →
[Agent CLI lifecycle](sphinx/agents.md) →
[Configuration](sphinx/configuration.md) →
[CLI reference](sphinx/cli.md) →
[Troubleshooting](sphinx/troubleshooting.md).
For pre-flight checks, see [Diagnostics](sphinx/diagnostics.md).

## Contributor route

- [`agents/README.md`](agents/README.md)
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
- [Sphinx developer internals](sphinx/developer-internals.md)
- [`mcp/` directory](mcp/) for MCP-specific debugging
- [Advanced MCP configuration](sphinx/advanced-mcp-configuration.md)

## Cross-tree role split

Two `docs/agents/` trees exist with distinct roles: `docs/agents/`
(repo-root) is contributor policy; this tree is agent-authoring
contracts. See [`../../docs/agents/README.md`](../../docs/agents/README.md)
and [`agents/README.md`](agents/README.md). Cross-link, do not duplicate.