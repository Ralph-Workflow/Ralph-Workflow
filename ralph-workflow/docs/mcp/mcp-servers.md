# MCP Servers

Ralph acts as an MCP proxy for all agents — configure once in `mcp.toml`, used by every agent.

## Config file locations

Ralph loads `mcp.toml` from three locations, in precedence order (highest to lowest):

| Priority | Location | Scope |
|---|---|---|
| 1 (highest) | `.agent/mcp.toml` | Project-local |
| 2 | `~/.config/ralph-workflow-mcp.toml` | User-global |
| 3 | Bundled default | Package-level |

When the same server name appears in multiple files, the higher-priority file wins and a warning is logged. The bundled default at `ralph/policy/defaults/mcp.toml` ships inside the wheel and provides commented example entries.

## Schema

Every server entry lives under `[mcp_servers.<name>]` and must conform to `McpServerSpec`.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | Yes | Server identifier. Pattern: `^[a-z][a-z0-9_-]*$`. **Must not be `ralph`** (reserved). Must not contain `__`. |
| `transport` | `"http"` or `"stdio"` | Yes | Transport protocol |
| `url` | `string \| null` | For `http` transport | HTTP endpoint URL |
| `command` | `string \| null` | For `stdio` transport | Executable path or command name |
| `args` | `list[string]` | No | Command-line arguments passed to `command` |
| `env` | `dict[string, string]` | No | Environment variables for `stdio` transport |
| `chains` | `null` | Reserved | **Reserved for v2; must be omitted in v1** |

### Name constraint

```toml
# Valid: lowercase alphanumerics, hyphens, underscores
[mcp_servers.my_server]
[mcp_servers.my_server_2]
[mcp_servers.my-server]

# Invalid — reserved name
[mcp_servers.ralph]  # ← raises ValueError

# Invalid — contains '__'
[mcp_servers.my__server]  # ← raises ValueError
```

### stdio transport example

```toml
[mcp_servers.github]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]

[mcp_servers.github.env]
GITHUB_TOKEN = "$GITHUB_TOKEN"
```

### http transport example

```toml
[mcp_servers.docs]
transport = "http"
url = "https://mcp.example.com/docs"
```

## Collision policy

If a server name exists in **both** `mcp.toml` **and** an agent's native config (`~/.claude.json`, `~/.codex/config.toml`, etc.), the `mcp.toml` entry wins and a warning is logged at startup:

```
warning: server "github" defined in mcp.toml overrides agent config; using mcp.toml definition
```

Agent-native config entries are not forwarded to the provider CLI. All user-defined servers are loaded by Ralph and re-exposed as Ralph-owned proxied tool aliases.

## Failure policy

Ralph validates every configured custom MCP server at startup by completing the standard `initialize` → `notifications/initialized` → `tools/list` handshake. If any server fails validation, Ralph exits with code 1 and logs the failure reason. Environment variable values defined under `[mcp_servers.<name>.env]` are never included in the failure output — only the variable names are surfaced.

```
ERROR  Custom MCP servers failed startup validation:
- github (transport=stdio) env_keys=['GITHUB_TOKEN']: HTTP request to '...' failed: ConnectError
```

To preserve the legacy warn-and-skip behaviour for CI smoke runs, set `RALPH_MCP_STRICT=0` in the environment:

```
RALPH_MCP_STRICT=0 ralph
```

In soft mode, failed servers are skipped (a warning is logged per failure) and the pipeline continues with only the reachable subset.

## Agent compatibility validation

After every upstream MCP server passes validation, Ralph synthesizes the per-agent transport wiring it would emit for Claude, Codex, and OpenCode and re-runs the same MCP handshake against each backend. This guarantees that what Ralph hands to each agent's MCP client can actually reach the same server. If any agent transport probe fails in strict mode, Ralph exits with code 1 and identifies the (server, transport) pair that failed.

The probe never spawns the agent binaries themselves — the MCP JSON-RPC protocol is identical across all supported agents (`2024-11-05`), so Ralph's own client is a faithful reference.

## Troubleshooting

Run `ralph --diagnose` to render the per-server `Custom MCP Servers` table and the `Agent Transport Compatibility` table. Both tables surface the redacted error string Ralph would emit during startup, so users can confirm credentials, command paths, and reachability without re-running the full pipeline.

## Forward compatibility

The `chains` field is **reserved for v2**:

```toml
# Do NOT use in v1 — schema accepts it but runtime ignores it
[mcp_servers.example]
transport = "stdio"
command = "echo"
chains = ["some-chain"]  # ← reserved; must be null/omitted today
```

In v1 the field must be omitted or set to `null`. The schema permits it to exist so that v1 configs remain valid when v2 ships.

## Worked example: GitHub MCP server

Add the GitHub MCP server to your `mcp.toml`:

```toml
[mcp_servers.github]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]

[mcp_servers.github.env]
GITHUB_TOKEN = "$GITHUB_TOKEN"
```

Ralph loads this server and re-exposes its tools under the `ralph_upstream__github__<tool_name>` alias namespace. For example, if the GitHub MCP server exposes `search_repositories` and `get_repo`, the agent sees them as:

```
ralph_upstream__github__search_repositories
ralph_upstream__github__get_repo
```

The `ralph_upstream__` prefix makes it unambiguous which upstream server owns each tool and prevents namespace collisions between providers.
