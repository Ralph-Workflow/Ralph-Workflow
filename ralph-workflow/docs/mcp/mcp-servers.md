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

### Validating manually

Use `ralph --check-mcp` to run the full startup validation + agent transport probe without starting the pipeline. The flag is a read-only pre-flight that exits `0` when every configured server passes (or when no `mcp.toml` is present) and `1` on any failure — the exact same logic the runner applies at phase 1:

```
ralph --check-mcp
```

`RALPH_MCP_STRICT=0` still relaxes the exit code to `0` while logging warnings per failure. When no custom MCP servers are configured, `--check-mcp` returns `0` immediately.

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

## Worked example: Angular CLI integration

Angular CLI ships an MCP server (`@angular/mcp`) that exposes workspace-aware build and scaffold tools. Add it to `.agent/mcp.toml`:

```toml
[mcp_servers.angular-cli]
transport = "stdio"
command = "npx"
args = ["-y", "@angular/mcp"]
```

The package name may change as the Angular MCP tooling matures; see the [Angular MCP README](https://angular.dev/tools/mcp) for the current name and any required flags.

Once configured, each agent sees the Angular tools under their proxy alias. For example, `generate` becomes:

```
ralph_upstream__angular-cli__generate
```

No other config is needed — Ralph handles the stdio handshake, tool discovery, and per-agent wiring automatically.

Verify the integration before running the pipeline:

```
ralph --check-mcp
```

Expected output (tools may vary by Angular CLI version):

```
Custom MCP Servers
  angular-cli  stdio  ok

Agent Transport Compatibility
  angular-cli × CLAUDE    ok
  angular-cli × CODEX     ok
  angular-cli × OPENCODE  ok
```

## Worked example: Docs MCP server on localhost:6280

[arabold/docs-mcp-server](https://github.com/arabold/docs-mcp-server) serves indexed documentation as MCP tools. Start it locally (it listens on port 6280 by default):

```
npx -y docs-mcp-server
```

Then add it to `.agent/mcp.toml`:

```toml
[mcp_servers.docs-mcp]
transport = "http"
url = "http://localhost:6280"
```

The server must be running before `ralph` (or `ralph --check-mcp`) starts. Ralph will fail startup validation if the server is unreachable. Use `RALPH_MCP_STRICT=0` during development if the docs server is optional.

Once running, the search and fetch tools are exposed as:

```
ralph_upstream__docs-mcp__search_documentation
ralph_upstream__docs-mcp__fetch_documentation
```

(Tool names depend on the docs-mcp-server version; run `ralph --check-mcp` to see the actual list.)
