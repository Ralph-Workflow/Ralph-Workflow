# Advanced MCP Configuration

This page is for operators who want to control Ralph Workflow’s **tool and external-integration layer**.
Use it when you need to wire in search, readable-page fetches, or custom MCP servers without muddling that work into the main workflow docs.
This layer should stay deliberate: plug Ralph Workflow into tools you already trust instead of turning integrations into hidden magic.

This is also where the trust boundary matters most.
Prefer plugging Ralph Workflow into tools you already trust and keep secrets out of repo-local config whenever you can.

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

> **Pi.dev has no native CLI MCP config file.** PiRuntimeResolver (in
> `ralph/agents/invoke/_runtime_resolvers/__init__.py`) removes
> `RALPH_MCP_ENDPOINT` from the Pi subprocess environment, writes a generated
> Pi extension, and passes that extension with `--no-builtin-tools --extension`.
> The extension registers Ralph Workflow MCP tools through Pi's custom-tool API and
> proxies calls to the active HTTP MCP endpoint. This is pinned by
> `tests/agents/invoke/test_pi_command_builder_and_runtime_resolver.py::TestPiRuntimeResolver`
> (`test_mcp_endpoint_in_extra_env_writes_extension`,
> `test_mcp_endpoint_in_base_env_writes_extension`). If a future pi.dev
> release adds a CLI MCP flag, update both the resolver and the tests in the
> same diff.

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
