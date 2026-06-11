# MCP Servers

Ralph Workflow acts as an MCP proxy for all agents — configure once in `mcp.toml`, used by every agent.

## Config file locations

Ralph Workflow loads `mcp.toml` from three locations, in precedence order (highest to lowest):

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

## Multimodal MCP Support (default-on)

Ralph Workflow supports broad multimodal MCP tools covering **images, PDFs, audio, video, and office documents**. This support is **enabled by default** to ensure seamless multimodal capability without configuration.

The primary entry point is `read_media`, which handles all supported modalities. `read_image` is a compatibility alias for `read_media` restricted to image inputs; it follows the same capability-aware delivery contract as `read_media`.

### Disabling multimodal support

To disable, add to your `mcp.toml`:

```toml
[media]
enabled = false
```

To customize without disabling:

```toml
[media]
enabled = true  # default, can be omitted
max_inline_bytes = 10485760  # 10 MiB to allow larger inline images
```

### How read_media works

When `media.enabled = true` (default), Ralph Workflow registers `read_media` as the primary multimodal tool. It:

- Reads images (PNG, JPEG, GIF, WebP), PDFs, audio (MP3, WAV, OGG, M4A, FLAC, AAC), video (MP4, AVI, MOV, MKV, WebM), and office documents (DOCX, PPTX, XLSX)
- Returns images as inline base64 MCP image blocks when the model supports it
- Returns PDFs and documents as typed blocks (e.g., `pdf` or `document` block type) for providers that support them natively (Claude, Gemini)
- Returns audio and video as typed blocks for providers that support them (Gemini); returns an explicit unsupported error for providers that do not (Claude, OpenAI/Codex)
- Returns all media as replayable `resource_reference` blocks for unknown providers, stored in the session manifest and retrievable via `ralph://media/...` URIs through `resources/read`
- Keeps the on-disk replay byte cache under `.agent/tmp/media/` bounded to 256 MiB, pruning older cache files instead of growing without limit
- Uses the session’s `ResolvedCapabilityProfile` — pre-computed at session start from the provider/model identity — to select the delivery mode for each modality (inline, typed-block, resource-reference, or explicit unsupported)
- Returns an explicit error when the modality is unsupported by the current provider/model

`read_image` is a compatibility alias for `read_media` restricted to image inputs; it follows the same capability-aware delivery contract (inline image when supported, resource reference or explicit error otherwise).

### Supported multimodal workflows

Ralph Workflow supports the following first-class multimodal workflow patterns:

- **Screenshot and browser-captured visual QA** — a browser automation tool captures a screenshot; Ralph Workflow preserves it as multimodal context and routes it to the model inline (for capable providers) or as a replayable `ralph://media/<id>` artifact retrievable via `resources/read`.
- **Mixed-modality execution** — workflows combining multiple modalities in a single run (e.g. screenshot + PDF context, audio + text artifacts, image + document metadata) are treated as normal platform use cases.
- **Replayable resource handles** — when inline delivery is unsupported, Ralph Workflow stores artifact bytes in the session manifest and returns a `ralph://media/<id>` URI retrievable via `resources/read`.
- **Document understanding** — PDFs and office documents are delivered as typed blocks (Claude, Gemini) or replayable resource references (unknown providers).
- **Audio and video understanding** — delivered as typed blocks for Gemini; other providers receive an explicit unsupported error.

### What text-only clients see

When a client connects without declaring multimodal support, the `read_media` and `read_image` tools are **automatically suppressed** from `tools/list` even if `media.enabled = true`. This ensures:

- Existing text-only clients continue to work unchanged
- Multimodal tools only appear for clients that declare image/media capability

### Client capability declaration

Clients declare multimodal support in the MCP `initialize` handshake via `capabilities`. Ralph Workflow extracts the following signals:

- `capabilities.image` — any truthy value
- `capabilities.media` — any truthy value
- `capabilities.multimodal` — any truthy value

If no signal is present, Ralph Workflow treats the client as text-only.

### Content block formats

Text content uses the standard MCP text block:

```json
{"type": "text", "text": "..."}
```

Small images for capable models use the MCP image block:

```json
{"type": "image", "data": "<base64>", "mimeType": "image/png"}
```

Typed blocks are used for PDFs, documents, audio, and video on providers that support them natively:

```json
{"type": "pdf", "source": {"type": "base64", "media_type": "application/pdf", "data": "<base64>"}}
```

For unknown providers, or when the artifact should remain retrievable via `resources/read`, a replayable resource-reference block is used:

```json
{"type": "resource_reference", "uri": "ralph://media/<id>", "mimeType": "application/pdf", "modality": "pdf", "title": "report.pdf", "delivery": "resource_reference_replay"}
```

The artifact bytes are retrievable via `resources/read` using the `ralph://media/<id>` URI while the live session manifest or bounded `.agent/tmp/media/` cache still has the bytes. If an old cache entry is pruned and no source file is available, replay returns an explicit missing-source error instead of silently reading stale data.

### Provider/modality delivery matrix

| Provider | Image | PDF | Document | Audio | Video |
|----------|-------|-----|----------|-------|-------|
| Claude/Anthropic | inline | typed block | typed block | unsupported | unsupported |
| Gemini | inline | typed block | typed block | typed block | typed block |
| OpenAI/Codex | inline (vision models) | unsupported | unsupported | unsupported | unsupported |
| Unknown | resource_reference_replay | resource_reference_replay | resource_reference_replay | resource_reference_replay | resource_reference_replay |

## Upstream multimodal normalization policy

When an upstream MCP server returns a multimodal content block (`image`, `audio`, `video`, `pdf`, or `document`), Ralph Workflow **normalizes it to a `resource_reference` block** rather than rejecting or silently stringifying it.

- **URI-backed blocks** (blocks with a `uri` or `source.uri` field): the upstream URI is preserved in the normalized `resource_reference`. The artifact bytes are not fetched or stored by Ralph.
- **Embedded-data blocks** (blocks with `data` or `source.data`): the bytes are stored in the active session manifest before a `ralph://media/...` URI is returned. The on-disk replay cache is bounded and prunes older cache files. This requires an active session; calls without a session raise an explicit error.

Malformed blocks (inconsistent MIME type vs declared type, or blocks with neither URI nor data) raise `UpstreamCallError` with a clear description. Unknown block types (e.g., `binary_blob`) also raise `UpstreamCallError`, listing the accepted types.

This policy prevents silent data loss while preserving multimodal context end to end through Ralph’s managed runtime path.

## Collision policy

If a server name exists in **both** `mcp.toml` **and** an agent's native config (`~/.claude.json`, `~/.codex/config.toml`, etc.), the `mcp.toml` entry wins and a warning is logged at startup:

```
warning: server "github" defined in mcp.toml overrides agent config; using mcp.toml definition
```

Agent-native config entries are not forwarded to the provider CLI. All user-defined servers are loaded by Ralph Workflow and re-exposed as Ralph Workflow-owned proxied tool aliases.

## Failure policy

Ralph Workflow validates every configured custom MCP server at startup by completing the standard `initialize` → `notifications/initialized` → `tools/list` handshake. If any server fails validation, Ralph Workflow exits with code 1 and logs the failure reason. Environment variable values defined under `[mcp_servers.<name>.env]` are never included in the failure output — only the variable names are surfaced.

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

After every upstream MCP server passes validation, Ralph Workflow synthesizes the per-agent transport wiring it would emit for Claude, Codex, OpenCode, and Google Anti Gravity and re-runs the same MCP handshake against each backend. This guarantees that what Ralph Workflow hands to each agent's MCP client can actually reach the same server. If any agent transport probe fails in strict mode, Ralph Workflow exits with code 1 and identifies the (server, transport) pair that failed.

The probe never spawns the agent binaries themselves — the MCP JSON-RPC protocol is identical across all supported agents (`2024-11-05`), so Ralph Workflow's own client is a faithful reference.

## Troubleshooting

Run `ralph --diagnose` to render the per-server `Custom MCP Servers` table and the `Agent Transport Compatibility` table. Both tables surface the redacted error string Ralph Workflow would emit during startup, so users can confirm credentials, command paths, and reachability without re-running the full pipeline.

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

Ralph Workflow loads this server and re-exposes its tools under the `ralph_upstream__github__<tool_name>` alias namespace. For example, if the GitHub MCP server exposes `search_repositories` and `get_repo`, the agent sees them as:

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

No other config is needed — Ralph Workflow handles the stdio handshake, tool discovery, and per-agent wiring automatically.

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
  angular-cli × AGY       ok
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
url = "http://localhost:6280/mcp"
```

Ralph Workflow also supports the legacy HTTP+SSE endpoint shape used by some docs-mcp setups:

```toml
[mcp_servers.docs-mcp]
transport = "http"
url = "http://localhost:6280/sse"
```

Prefer `/mcp` when you control the server config. Use `/sse` only when the server exposes the older HTTP+SSE flow.

The server must be running before `ralph` (or `ralph --check-mcp`) starts. Ralph Workflow will fail startup validation if the server is unreachable. Use `RALPH_MCP_STRICT=0` during development if the docs server is optional.

Once running, the search and fetch tools are exposed as:

```
ralph_upstream__docs-mcp__search_documentation
ralph_upstream__docs-mcp__fetch_documentation
```

(Tool names depend on the docs-mcp-server version; run `ralph --check-mcp` to see the actual list.)

## Worked example: Crawl4AI advanced web crawling

[Crawl4AI](https://docs.crawl4ai.com/) is an MCP server for advanced web crawling — multi-page,
JavaScript-rendered sites, structured data extraction. It is the recommended choice when the
built-in `visit_url` tool (which fetches a single static page) is not enough.

### Install and start

```
pip install crawl4ai
crawl4ai-mcp
```

By default the server listens on port 11235. Add it to `.agent/mcp.toml`:

```toml
[mcp_servers.crawl4ai]
transport = "http"
url = "http://localhost:11235/mcp"
```

Ralph Workflow already supports upstream MCP servers, so no additional code is needed. The Crawl4AI tools
are exposed as:

```
ralph_upstream__crawl4ai__crawl
ralph_upstream__crawl4ai__crawl_many
```

(Exact tool names depend on the installed Crawl4AI version; run `ralph --check-mcp` to see the
actual list.)

### Security notes

Crawl4AI can execute JavaScript and follow redirects across many pages. Run it on a network
interface that is not reachable from untrusted sources, or use a dedicated container. The
built-in `visit_url` SSRF guard does **not** apply to upstream MCP server calls — firewall rules
at the OS level are the right control for Crawl4AI in production.

### When to use which

| Need | Tool |
|---|---|
| Fetch one static HTML page | `visit_url` (built-in, no setup) |
| JavaScript-rendered SPA | `ralph_upstream__crawl4ai__crawl` |
| Multi-page crawl / sitemap | `ralph_upstream__crawl4ai__crawl_many` |
| Structured extraction (CSS/JSON-LD) | `ralph_upstream__crawl4ai__crawl` with extraction schema |

### Verifying cross-phase visibility

After configuring Crawl4AI, verify that its tools are visible across all Ralph Workflow phases:

```
ralph --check-mcp
```

Expected output:

```
Custom MCP Servers
  crawl4ai  http  ok

Agent Transport Compatibility
  crawl4ai × CLAUDE    ok
  crawl4ai × CODEX     ok
  crawl4ai × OPENCODE  ok
  crawl4ai × AGY       ok
```

If a phase fails to expose `ralph_upstream__crawl4ai__*` tools, run `ralph --diagnose` first — this is the regression failure mode that `tests/integration/test_web_access_phase_visibility.py` guards against.

The upstream proxy tools are registered when the session has `UPSTREAM_TOOL_USE` capability. This capability is granted to **all 10 session drains** by default, so configured upstream crawlers should be visible in every phase without additional configuration.

### Firecrawl as a heavier alternative

[Firecrawl](https://firecrawl.dev/) is a self-hosted crawling platform that also ships an MCP
server. It is viable as a local sidecar for teams that need advanced crawl features, but it is
**operationally heavier** than Crawl4AI:

- Firecrawl's self-hosted scrape/crawl endpoints are available without cost, but its
  browser/agent/cloud endpoints require the hosted service and are not available self-hosted.
- Running Firecrawl self-hosted requires more infrastructure (Docker, memory, CPU) than a
  lightweight Crawl4AI setup.
- Firecrawl is best suited for teams that already run Firecrawl in their stack and want to
  integrate it with Ralph Workflow rather than teams adopting a crawler for the first time.

If you already run Firecrawl, configure it like any other HTTP MCP server:

```toml
[mcp_servers.firecrawl]
transport = "http"
url = "http://localhost:3002/mcp"
```

For new projects, prefer **Crawl4AI** as the recommended local crawler — it is lighter,
MCP-native, and easier to operate.
