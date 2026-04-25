# Local Web Access

Ralph Workflow provides three distinct web capabilities — search, visit, and crawl — that are designed to be complementary rather than overlapping. Understanding what each does is essential for choosing the right tool.

## Search vs Visit vs Crawl

| Concept | Ralph Workflow Tool | What it Returns |
|---|---|---|
| **Web Search** | `web_search` | Titles, URLs, and snippets for matching pages |
| **Visit URL** | `visit_url` | Extracted readable text from a single page |
| **Crawl Site** | `ralph_upstream__crawl4ai__crawl` (via upstream) | Multi-page traversal and structured extraction |

Ralph Workflow can **search** the web, **visit** a page directly, and, when needed, **delegate crawling** to a stronger local crawler.

## Native vs Upstream

### Native: `visit_url` (built-in)

The `visit_url` tool is Ralph Workflow's built-in page reader. It fetches a single URL and returns clean, extracted text without requiring any additional services.

**When to use it:**
- Reading a single documentation page
- Inspecting a changelog, release note, or issue page
- Extracting content from a URL returned by `web_search`

**No setup required** — it ships with Ralph Workflow and works out of the box.

### Upstream: Crawl4AI / Firecrawl (local sidecar)

For multi-page crawls, JavaScript-rendered SPAs, or structured data extraction, Ralph Workflow can delegate to a local upstream MCP server.

**When to use it:**
- Crawling an entire documentation site
- Scraping JavaScript-heavy Single Page Applications
- Extracting structured data using CSS selectors or JSON-LD schemas

**Configuration required** — you run the crawler locally and register it in `.agent/mcp.toml`. See `docs/mcp/mcp-servers.md` (section "Worked example: Crawl4AI advanced web crawling") for a worked example.

### Choosing between them

| Need | Tool |
|---|---|
| Fetch one static HTML page | `visit_url` (built-in) |
| JavaScript-rendered SPA | `ralph_upstream__crawl4ai__crawl` |
| Multi-page crawl | `ralph_upstream__crawl4ai__crawl_many` |
| Structured extraction | `ralph_upstream__crawl4ai__crawl` with extraction schema |

## Local-First and Safety Posture

### SSRF Guard

The built-in `visit_url` tool implements SSRF (Server-Side Request Forgery) protection. By default, it blocks requests to:

- Loopback addresses (`127.0.0.0/8`, `::1`)
- Private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- Link-local addresses (`169.254.0.0/16`)
- Multicast and reserved ranges

This means `visit_url` will reject requests to `localhost`, `127.0.0.1`, or any private IP by default. This is intentional — it prevents accidental exposure of internal services.

### When to Enable Private Networks

Set `allow_private_networks = true` in `[web_visit]` config when:

- Ralph Workflow runs in an isolated container or VM with no internal service exposure
- You explicitly want Ralph Workflow to fetch from local development servers
- CI runners with dedicated network namespaces

**Trade-off:** Enabling private network access removes the SSRF guard. Only enable it when you understand the implications and your environment is appropriately isolated.

### Timeouts and Size Limits

`visit_url` enforces:
- A 15-second default timeout per request
- A 2 MiB maximum response body size

These prevent runaway fetch operations from consuming resources.

## Phase Visibility

The `WebVisit` capability is granted to **every session drain** by default. This means `visit_url` is visible and callable in:

- `planning`
- `development`
- `development_analysis`
- `development_commit`
- `analysis`
- `review`
- `review_analysis`
- `review_commit`
- `fix`
- `commit`

This default exposure is verified by the regression test `tests/integration/test_web_access_phase_visibility.py`, which confirms both `tools/list` visibility and `tools/call` callability across every drain.

Similarly, `UPSTREAM_TOOL_USE` is now granted to every drain by default, ensuring that upstream proxy tools (such as `ralph_upstream__crawl4ai__crawl`) are visible whenever an upstream crawler is configured.

## Naming Clarity

The canonical tool names exposed through Ralph Workflow's MCP surface are:

- `web_search` — multi-backend web search (ddgs, Tavily)
- `visit_url` — single-page fetch and extraction
- `ralph_upstream__crawl4ai__crawl` — Crawl4AI multi-page crawling (when configured)
- `ralph_upstream__crawl4ai__crawl_many` — Crawl4AI batch crawling (when configured)

Tool aliases are available for clients that use the `mcp__ralph__<tool>` prefix format. See `ralph.mcp.tools.names` for details.

## Further Reading

For detailed `visit_url` configuration and response formats, see `docs/mcp/web-visit.md`.

For Crawl4AI worked example with cross-phase verification, see `docs/mcp/mcp-servers.md`.
