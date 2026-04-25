# Configuration Reference

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Ralph Workflow uses a layered configuration system. Settings are resolved in this order
(highest priority first):

1. **CLI flags** — override everything
2. **Project-local config** — `.agent/ralph-workflow.toml` in the workspace root
3. **User-global config** — `~/.config/ralph-workflow.toml`
4. **Bundled defaults** — shipped inside the package at `ralph/policy/defaults/`

## Config Files

Ralph Workflow manages seven config files across two scopes.

### User-Global (created once, shared across projects)

| File | Purpose |
|------|---------|
| `~/.config/ralph-workflow.toml` | Global defaults: agent selection, iteration counts, verbosity |
| `~/.config/ralph-workflow-mcp.toml` | MCP server definitions shared across all projects |

### Project-Local (created per project in `.agent/`)

| File | Purpose |
|------|---------|
| `.agent/ralph-workflow.toml` | Project-specific overrides for the main config |
| `.agent/mcp.toml` | Project-specific MCP server definitions |
| `.agent/agents.toml` | Agent definitions, chains, and drain bindings |
| `.agent/pipeline.toml` | Phase sequence and parallel execution settings |
| `.agent/artifacts.toml` | Artifact type schemas and contracts |

Run `ralph --init` to create all of these from the bundled templates.

## Bundled Default Templates

The bundled defaults live in `ralph/policy/defaults/`. Each file contains inline comments
explaining every field. The canonical reference is the file itself:

- `ralph-workflow.toml` — general config (iterations, review depth, verbosity, isolation)
- `mcp.toml` — empty MCP server list (add custom servers here)
- `agents.toml` — default agent definitions (`claude`, `opencode`), chains, and drains
- `pipeline.toml` — default phase sequence and parallel execution policy
- `artifacts.toml` — artifact type contracts

## Regenerating Configs

```bash
ralph --regenerate-config
```

Rewrites all configs from the bundled templates. Existing files are backed up with a
`.bak` suffix before being overwritten, so no data is lost.

## Frequently Asked Questions

### I have no agents installed

Ralph Workflow will start but will fail when it tries to invoke an agent. Install at
least one supported agent:

- **Claude Code**: see <https://docs.claude.com/claude-code>
- **opencode**: see <https://opencode.ai>

Then verify with `ralph --diagnose`.

### I want to use a single agent only

Edit `.agent/agents.toml`. Find the `[agent_chains.developer]` and
`[agent_chains.reviewer]` sections and set `agents = ["your-agent"]` in each.
Remove any fallover entries you do not need.

### How do I add a custom MCP server

Add a `[[servers]]` entry to `.agent/mcp.toml`:

```toml
[[servers]]
name = "my-server"
command = ["npx", "my-mcp-server"]
```

Validate with `ralph --check-mcp` after editing.
