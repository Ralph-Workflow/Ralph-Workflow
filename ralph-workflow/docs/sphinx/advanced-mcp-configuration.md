# Advanced MCP Configuration

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This page is for operators who want to control Ralph Workflow’s **tool and external-integration layer**.

## Which file am I editing?

- project-local MCP overrides → `.agent/mcp.toml`
- user-global MCP defaults → `~/.config/ralph-workflow-mcp.toml`
- bundled default / example → `ralph/policy/defaults/mcp.toml`

Use project-local config when a repo needs custom MCP servers. Use user-global config when you want the same servers available everywhere.

## What `mcp.toml` controls

`mcp.toml` configures:

- MCP servers over `stdio` or `http`
- search backends
- web-visit / readable-page fetch behavior
- media handling toggles
- advanced crawling integrations

## Major sections

### `[mcp_servers.<name>]`

Defines a named MCP server.

Common fields:

- `transport`
- `command`
- `args`
- `url`
- `env`

### `[web_search]`

Controls whether search is enabled and which backend/fallback chain is used.

### `[web_search.backends.<name>]`

Backend-specific configuration.

### `[web_visit]`

Controls readable-page fetch behavior.

Typical fields include:

- `enabled`
- `timeout_ms`
- `max_bytes`
- `user_agent`
- `allow_private_networks`
- `extract_links`

### `[media]`

Controls broad multimodal file reading behavior.

## Common advanced user stories

### I want to add a GitHub/docs/custom MCP server

Add a new `[mcp_servers.<name>]` block.

### I want different MCP servers in one repo than in the rest of my machine

Use `.agent/mcp.toml`.

### I want web search enabled with explicit backends

Edit `[web_search]` and the backend-specific blocks.

### I want to wire in Crawl4AI or another HTTP MCP service

Add an `http` MCP server block pointing at the service URL.

## Safety rules

- keep this file secret-free where possible
- prefer `api_key_env` or environment variables over inline secrets
- do not commit real credentials into repo-local `.agent/mcp.toml`

## Related

- [Configuration Reference](configuration.md)
- [MCP Tools](mcp-tools.md)
- [Local Web Access](local-web-access.md)
