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

When an upstream MCP server is unreachable at launch time, Ralph logs a warning and skips that server:

```
warning: upstream server "github" is unreachable; skipping its tools
```

The agent session continues without the unavailable server's tools. There is no retry of the same backend within a single session.

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
