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

## Web access (search, visit, crawl)

Ralph Workflow supports three related web capabilities: **search**, **visit**, and **crawl**. They solve different problems, so the fastest way to stay productive is to pick the smallest tool that fits the job.

| Need | Tool | What you get |
|---|---|---|
| Find relevant pages | `web_search` | Titles, URLs, and snippets |
| Read one page | `visit_url` | Extracted readable text from a single URL |
| Traverse many pages or scrape a JS-heavy site | upstream crawler | Multi-page traversal and structured extraction |

### The default choice: `visit_url`

`visit_url` is Ralph Workflow's built-in page reader. It fetches a single URL and returns readable extracted text without any extra setup. Use it to read one documentation page, inspect a changelog or release note, or follow up on a URL returned by `web_search`. It ships with Ralph Workflow and works out of the box.

### When to use an upstream crawler

For multi-page crawls, JavaScript-rendered SPAs, or structured extraction, Ralph Workflow can delegate to a local upstream MCP server. Use an upstream crawler when you need to crawl an entire docs site, scrape a JavaScript-heavy application, or extract structured content with selectors or schemas. This path requires extra configuration in `.agent/mcp.toml` because the crawler runs locally as a separate service.

### Choosing between them

| Need | Tool |
|---|---|
| Fetch one static HTML page | `visit_url` |
| JavaScript-rendered SPA | `ralph_upstream__crawl4ai__crawl` |
| Multi-page crawl | `ralph_upstream__crawl4ai__crawl_many` |
| Structured extraction | `ralph_upstream__crawl4ai__crawl` with an extraction schema |

### Safety posture

#### SSRF guard

The built-in `visit_url` tool blocks requests to loopback, private-network, link-local, multicast, and other reserved address ranges by default. That means it will reject `localhost`, `127.0.0.1`, and private IPs unless you explicitly relax the guard. This is intentional: it reduces the risk of exposing internal services by accident.

#### When to enable private-network access

Set `allow_private_networks = true` in `[web_visit]` only when you understand the trade-off and the environment is appropriately isolated. Typical cases: Ralph Workflow runs in an isolated container or VM; you intentionally want access to local development servers; or a CI runner has its own dedicated network boundary.

#### Timeouts and size limits

`visit_url` enforces a 15-second default timeout per request and a 2 MiB maximum response body size. These limits keep fetch operations from growing into unbounded background work.

### Tool names you will see

- `web_search` — search the web
- `visit_url` — read one page
- `ralph_upstream__crawl4ai__crawl` — crawl through an upstream crawler when configured
- `ralph_upstream__crawl4ai__crawl_many` — batch crawling through that upstream crawler

## Multimodal delivery contract

Ralph Workflow's `read_media` tool (with `read_image` as a compatibility alias) is the primary multimodal entry point. The contract below is enforced by `mcp.toml` and the per-session `ResolvedCapabilityProfile`; it covers inline, typed-block, and resource-reference delivery for every provider Ralph Workflow supports.

### How `read_media` works

When `media.enabled = true` (default), Ralph Workflow registers `read_media` as the primary multimodal tool. It:

- Returns images as inline base64 blocks for providers that support inline images
- Returns PDFs and documents as typed blocks (e.g., `pdf` or `document` block type) for providers that support them natively (Claude, Gemini)
- Returns audio and video as typed blocks for providers that support them (Gemini); returns an explicit unsupported error for providers that do not (Claude, OpenAI/Codex)
- Returns all media as replayable `resource_reference` blocks for unknown providers, stored in the session manifest and retrievable via `ralph://media/...` URIs through `resources/read`
- Uses the session's `ResolvedCapabilityProfile` — pre-computed at session start from the provider/model identity — to select the delivery mode for each modality (inline, typed-block, resource-reference, or explicit unsupported)
- Returns an explicit error when the modality is unsupported by the current provider/model

`read_image` is a compatibility alias for `read_media` restricted to image inputs; it follows the same capability-aware delivery contract (inline image when supported, resource reference or explicit error otherwise).

### Provider/modality delivery matrix

| Provider | Image | PDF | Document | Audio | Video |
|---|---|---|---|---|---|
| Claude/Anthropic | inline | typed block | typed block | unsupported | unsupported |
| Gemini | inline | typed block | typed block | typed block | typed block |
| OpenAI/Codex | inline (vision models) | unsupported | unsupported | unsupported | unsupported |
| Unknown | resource_reference_replay | resource_reference_replay | resource_reference_replay | resource_reference_replay | resource_reference_replay |

This matrix is the canonical reference for which provider/modality combinations are explicitly unsupported and which fall back to a replayable resource-reference block.

### Common workflows

- **Screenshot and browser-captured visual QA** — a browser automation tool captures a screenshot; Ralph Workflow preserves it as multimodal context and routes it to the model inline (for capable providers) or as a replayable `ralph://media/<id>` artifact retrievable via `resources/read`.
- **Mixed-modality execution** — workflows combining multiple modalities in a single run (e.g. screenshot + PDF context, audio + text artifacts, image + document metadata) are treated as normal platform use cases.
- **Replayable resource handles** — when inline delivery is unsupported, Ralph Workflow stores artifact bytes in the session manifest and returns a `ralph://media/<id>` URI retrievable via `resources/read`.
- **Document understanding** — PDFs and office documents are delivered as typed blocks (Claude, Gemini) or replayable resource references (unknown providers).
- **Audio and video understanding** — delivered as typed blocks for Gemini; other providers receive an explicit unsupported error.

### What text-only clients see

When a client connects without declaring multimodal support, the `read_media` and `read_image` tools are **automatically suppressed** from `tools/list` even if `media.enabled = true`. This ensures text-only clients continue to work unchanged; multimodal content is not silently stringified or rejected.

### Upstream normalization

When an upstream MCP server returns a multimodal content block (`image`, `audio`, `video`, `pdf`, or `document`), Ralph Workflow **normalizes it to a `resource_reference` block** rather than rejecting or silently stringifying it.

- **URI-backed blocks** (blocks with a `uri` or `source.uri` field): the upstream URI is preserved in the normalized `resource_reference`. The artifact bytes are not fetched or stored by Ralph Workflow.
- **Embedded-data blocks** (blocks with `data` or `source.data`): the bytes are stored in the active session manifest before a `ralph://media/...` URI is returned. The on-disk replay cache is bounded and prunes older cache files. This requires an active session; calls without a session raise an explicit error.

## Related

- [Configuration Reference](configuration.md)
- [MCP Tools](mcp-tools.md)
