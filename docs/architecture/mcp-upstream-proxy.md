# MCP Upstream Proxy Architecture

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document describes the Ralph upstream proxy architecture for the Python implementation.

**Status**: Design and partial implementation — implementation has started; the upstream config model (`ralph-workflow/ralph/mcp/upstream_config.py`) is implemented as of Task 2. Remaining tasks add the proxy client, registry, and runtime integration.

**Supersedes**: Nothing formal; this is a new component

---

## 1. Overview

Ralph operates a run-scoped MCP server that is the only MCP endpoint visible to provider CLIs. When a user has configured upstream MCP servers (for example, in their `~/.claude.json`, `~/.opencode/mcp.json`, or equivalent), Ralph loads those server definitions itself and re-exposes their tools as Ralph-owned proxied aliases. Provider CLIs never receive the upstream server definitions directly.

This contract holds across all supported transports: Claude, OpenCode, Codex, and Google Anti Gravity.

### What is proxied in v1

Only `tools` are proxied. `resources` and `prompts` are out of scope for v1.

### Naming

Proxied tools use explicit namespacing. The format is:

```
ralph_upstream__<server_name>__<tool_name>
```

Example:

```
ralph_upstream__filesystem__read_file
ralph_upstream__github__search_repos
```

This naming scheme is Ralph-owned and guaranteed not to collide with Ralph-native tool names.

---

## 2. Startup and Discovery

At run start, Ralph discovers upstream MCP server definitions from supported config sources:

- Claude: `~/.claude.json`, workspace `.mcp.json`, workspace `.claude.json`
- OpenCode: provider-specific config file paths
- Codex: provider-specific config file paths
- Google Anti Gravity: `~/.gemini/antigravity-cli/mcp_config.json`

Ralph reads these sources, extracts non-Ralph server entries, normalizes them into a transport-neutral model, and discards the raw definitions from provider-facing config. For Claude, OpenCode, and Codex: each transport receives a provider-visible MCP config that contains only Ralph (written by Ralph via environment-variable injection), while the upstream server definitions are passed to the Ralph runtime via a separate serialized payload. For Google Anti Gravity: Ralph injects a run-scoped Ralph-only MCP config into AGY's workspace config before launch and restores the original file afterward; Ralph reads upstream server definitions from the user's existing AGY config files and re-exposes them as proxied aliases via Ralph's upstream proxy.

This separation means provider-side MCP permissions are never the authority for proxied tools. Ralph may still use provider-side approval surfaces to pre-approve Ralph-owned MCP tool names for the current session, but Ralph remains the single policy boundary for actual authorization.

---

## 3. Upstream Client Lifecycle

Each upstream server has a dedicated client that lives for the duration of the Ralph run:

1. **Initialization** — at startup, Ralph validates upstream server config. If an upstream server fails to initialize, it is omitted from the advertised catalog (fail-closed).
2. **Tool catalog fetch** — after successful initialization, the client fetches the upstream server's tool list.
3. **Registration** — each upstream tool is registered under its Ralph-owned alias.
4. **Tool call dispatch** — when a provider calls a proxied tool, Ralph enforces its capability policy first, then forwards the call to the upstream client.
5. **Shutdown** — upstream clients are torn down when the Ralph run ends.

Supported upstream transports: HTTP (MCP over HTTP) and stdio (MCP over subprocess pipes).

---

## 4. Namespacing and Collision Handling

### Alias construction

Every proxied tool is renamed using the `ralph_upstream__<server_name>__<tool_name>` scheme. This guarantees:

- Ralph-native tools are never displaced.
- Two upstream servers with the same tool name produce distinct aliases.
- The prefix `ralph_upstream__` is reserved; no Ralph-native tool may use it.

### Collision detection

Ralph rejects duplicate proxy aliases deterministically at startup. If two upstream servers would produce the same Ralph-owned alias, the startup is aborted with a clear error identifying the collision.

### Server name normalization

Server names used in the alias are derived from the upstream server's declared name, with characters that are invalid in tool identifiers replaced or removed.

---

## 5. Policy Enforcement Order

Every proxied tool call passes through two enforcement layers, in order:

1. **Ralph capability check** — before forwarding, Ralph verifies the session holds the required capability (for example, `UpstreamToolUse`). If the session lacks the capability, the call is denied by Ralph, not by the provider.
2. **Upstream call** — if Ralph allows the call, it is forwarded to the upstream client with the original arguments.

Provider-side MCP permissions may pre-approve Ralph-owned MCP tool names so the provider stops prompting, but they are not authoritative for proxied tool calls. The provider CLI knows only about the Ralph MCP server; it never directly calls the upstream server.

Backend auth (for example, upstream server API keys or tokens) is preserved by the upstream client and is not replaced or bypassed by Ralph.

---

## 6. Failure Behavior

### Startup failure (fail-closed)

If an upstream server cannot be initialized at startup (connection refused, invalid config, missing binary for stdio), that server's tools are **not advertised**. Ralph starts successfully with the servers that did initialize. A warning is logged identifying the failed upstream.

### Runtime tool call failure

If an upstream tool call fails at runtime, the error is forwarded with server name context:

```
upstream server '<server_name>' tool '<tool_name>' failed: <original_message>
```

Ralph does not suppress or remap upstream errors.

### Unreachable upstream during tool call

If the upstream server becomes unreachable after startup, tool calls to its proxied aliases return an error indicating the server is unavailable. Ralph does not attempt to restart upstream processes in v1.

---

## 7. Provider Integration Per Transport

### Claude

Provider-visible `--mcp-config` contains only the Ralph MCP server entry. User upstream server definitions are extracted from supported config sources and passed to the Ralph runtime via a serialized environment payload. Claude additionally receives a provider-side allowlist built from the exact live Ralph MCP tool names for that session so Ralph-owned tools do not trigger per-call approval prompts. The `UpstreamMcpServer` model is the canonical runtime representation.

### OpenCode

Provider-visible `OPENCODE_CONFIG_CONTENT` contains only Ralph as the MCP server. User upstream definitions are extracted and passed to Ralph via a separate runtime payload. Ralph-native tool disabling and MCP merging follow the same pattern as Claude.

### Codex

Provider-visible `config.toml` contains only the Ralph MCP server entry. Upstream server definitions are extracted and passed to Ralph via a serialized runtime payload. `[features]` settings follow the same best-effort approach as the existing native-tool restriction for Codex.

### Google Anti Gravity

Ralph writes a run-scoped Ralph-managed MCP config into AGY's workspace `.agents/mcp_config.json` before launch and restores the original file after the run:

- Global: `~/.gemini/antigravity-cli/mcp_config.json`
- Workspace: `.agents/mcp_config.json`

Ralph reads upstream server definitions from the user's existing AGY config files using `load_existing_agy_upstream_servers`, normalizes them, and re-exposes them as proxied tool aliases via Ralph's upstream proxy. Ralph does not write to or replace the user's live AGY config files.

No manual pre-configuration is needed. Ralph injects the run-scoped `.agents/mcp_config.json` automatically before each AGY run and restores the original file afterward. Run `ralph --check-mcp` to verify the wiring. See `ralph/mcp/transport/agy.py` for the AGY runtime injection implementation.

---

## 8. What Is Not in Scope for v1

- Proxying `resources` or `prompts` from upstream servers
- Live tool catalog refresh (upstream tools are fetched once at startup)
- Automatic upstream process restart
- Backend auth delegation (Ralph preserves upstream auth but does not manage it)
- Per-server or per-tool capability granularity (v1 uses a single `UpstreamToolUse` gate)

---

## 9. Relationship to Existing Docs

This doc supersedes the "strict mode" sections of `ralph-workflow/docs/mcp-tool-restriction.md` and extends the architecture notes in `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md` with the concrete proxy contract.

For the run-scoped MCP transport architecture, see `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md`. For per-CLI enforcement details, see `ralph-workflow/docs/mcp-tool-restriction.md`.
