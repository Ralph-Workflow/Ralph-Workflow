# Web Visit

The `visit_url` tool fetches a single URL and returns readable extracted text.
It is a built-in Ralph MCP tool gated by the `WebVisit` capability.

## Requirements

The tool uses two optional Python packages for HTML extraction:

```
pip install "ralph-workflow[web-visit]"
```

This installs:
- `readability-lxml` — main-content isolation
- `selectolax` — fast plain-text rendering and link extraction

If these packages are not installed, `visit_url` will still appear in `tools/list`
(because the capability is granted) but every call will return `is_error=true`
with a clear "install web-visit extras" message.

## Configuration

Add a `[web_visit]` section to `.agent/mcp.toml` to override defaults:

```toml
[web_visit]
enabled = true
timeout_ms = 15000
max_bytes = 2097152
user_agent = "RalphWorkflow/1.0 (+https://ralph-workflow.dev)"
allow_private_networks = false
extract_links = false
```

### Fields

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Toggle the tool on/off in the registry |
| `timeout_ms` | `15000` | Request timeout in milliseconds |
| `max_bytes` | `2097152` | Maximum response body size (2 MiB) |
| `user_agent` | `RalphWorkflow/1.0 …` | User-Agent header sent with every request |
| `allow_private_networks` | `false` | Block private/loopback/link-local IPs (SSRF guard) |
| `extract_links` | `false` | Whether to include outbound links in results by default |

## Tool input schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `url` | `string` | Yes | URL to fetch; must use `http` or `https` |
| `with_links` | `boolean` | No | Return up to 100 absolute outbound links (overrides config default) |

## Response format

On success (`is_error=false`):

```json
{
  "status": "ok",
  "title": "Page Title",
  "effective_url": "https://example.com/page",
  "content_type": "text/html; charset=utf-8",
  "text": "Extracted readable text …"
}
```

When `with_links=true`, a `links` array is also included:

```json
{
  "status": "ok",
  "title": "Page Title",
  "effective_url": "https://example.com/page",
  "content_type": "text/html; charset=utf-8",
  "text": "Extracted readable text …",
  "links": ["https://example.com/page-2", "https://other.example.com/"]
}
```

On failure (`is_error=true`):

```json
{
  "status": "timeout",
  "error": "request timed out",
  "effective_url": null,
  "http_status": null
}
```

### Status values

| Status | Meaning |
|---|---|
| `ok` | Fetch and extraction succeeded |
| `timeout` | Request exceeded `timeout_ms` |
| `unreachable` | DNS failure or connection refused |
| `http_error` | HTTP response code outside 200–299 |
| `unsupported_content` | Content-Type not in `text/html`, `text/plain`, `application/xhtml+xml`, etc. |
| `too_large` | Response body exceeded `max_bytes` |
| `blocked_by_policy` | Host resolved to a private/loopback address and `allow_private_networks=false` |
| `invalid_url` | URL scheme is not `http` or `https`, or hostname is missing |

## Capability and default grant

`visit_url` requires the `WebVisit` capability. Unlike `web_search`, which is
withheld from the `analysis` and `commit` drains, `WebVisit` is granted to
**all 10 session drains** by default.

## Private-network access (SSRF guard)

By default, `allow_private_networks=false` blocks any URL that resolves to a
loopback, private, link-local, multicast, or reserved address. Enable with
care — only when Ralph runs in a fully isolated environment:

```toml
[web_visit]
allow_private_networks = true
```

Failure category is logged at WARNING level; the URL itself is never logged.

## Advanced: multi-page and JavaScript-rendered crawling

For multi-page crawls or sites that require JavaScript rendering, use
[Crawl4AI](https://docs.crawl4ai.com/) as an upstream MCP server.
See [MCP Servers](mcp-servers.md#worked-example-crawl4ai-advanced-web-crawling)
for the configuration.
