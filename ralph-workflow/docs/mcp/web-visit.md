# Web Visit

The `visit_url` tool fetches a single URL and returns readable extracted text.
It is a built-in Ralph Workflow MCP tool gated by the `WebVisit` capability.

See [Local Web Access](../sphinx/local-web-access.md) for the product-level overview
of how `visit_url` fits into Ralph Workflow's three web concepts (search, visit, crawl).

## Requirements

The tool requires `readability-lxml` and `selectolax` for HTML extraction. Both are included in the default `ralph-workflow` installation.

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

`visit_url` requires the `WebVisit` capability. `WebVisit` is granted to
**all 10 session drains** by default, meaning `visit_url` is visible and callable
in every phase. This default exposure is verified by the cross-phase regression test
`tests/integration/test_web_access_phase_visibility.py`.

## Private-network access (SSRF guard)

### Policy decision

By default, Ralph Workflow treats localhost/private networks differently from the public internet:
they are **blocked unless the operator explicitly opts in**. This is an intentional
security posture — Ralph Workflow should not be able to reach internal services unless you
deliberately enable it.

When `allow_private_networks=false` (the default), `visit_url` blocks any URL that
resolves to:

| Address class | Examples |
|---|---|
| Loopback | `127.0.0.0/8`, `::1` |
| Private networks | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` |
| Link-local | `169.254.0.0/16`, `fe80::/10` |
| Multicast | `224.0.0.0/4`, `ff00::/8` |
| Reserved | `240.0.0.0/4`, `2000::/3` |
| Unspecified | `0.0.0.0`, `::` |
| Special hostname | `localhost` |

This list is implemented in `ralph.mcp.webvisit.fetcher._is_private_address` and
mirrors the SSRF guard logic from established secure HTTP fetch libraries.

### When to enable private-network access

Set `allow_private_networks = true` only when:

- Ralph Workflow runs in a dedicated container or VM with network isolation
- You explicitly want Ralph Workflow to fetch from local development servers
- CI runners have a dedicated network namespace with no internal service exposure

**Trade-off:** Enabling private network access removes the SSRF boundary between Ralph Workflow
and your internal services. Only enable it when your deployment environment is
appropriately isolated and you understand the implications.

```toml
[web_visit]
allow_private_networks = true
```

Failure category (`blocked_by_policy`) is logged at WARNING level; the URL itself
is never logged.

## Advanced: multi-page and JavaScript-rendered crawling

For multi-page crawls or sites that require JavaScript rendering, use
[Crawl4AI](https://docs.crawl4ai.com/) as an upstream MCP server.
See [MCP Servers](mcp-servers.md#worked-example-crawl4ai-advanced-web-crawling)
for the configuration.
