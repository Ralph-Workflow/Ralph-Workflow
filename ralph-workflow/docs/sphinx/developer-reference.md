# Developer Reference

This section is for contributors, maintainers, and integrators who need Ralph Workflow's
internal architecture, pipeline internals, prompt/runtime behavior, or Python package
surface.

If you only need to install, configure, and run Ralph Workflow, start with
[Getting Started](getting-started.md), [CLI Reference](cli.md), and
[Configuration](configuration.md) instead.

## What lives here

- [Operator Reference](reference.md) stays focused on command, config, and tool lookup
- [Developer Internals](developer-internals.md) groups the maintainer-facing architecture and runtime pages (including Agents Architecture, MCP Architecture, Artifacts, Prompts, and Transcript)
- [Policy-Driven Overhaul Migration](policy-driven-overhaul-migration.md) covers migration details and deeper policy-model background
- [Python API Reference](modules.rst) documents the public `ralph.*` package surface
- [Release & Versioning](versioning.md) covers release, tagging, and publishing policy

```{toctree}
:maxdepth: 1

developer-internals
policy-driven-overhaul-migration
modules
versioning
```

## Related pages

- [CLI Reference](cli.md) — command surface and flags
- [Configuration](configuration.md) — runtime policy and config precedence
- [Versioning](versioning.md) — release and compatibility policy
