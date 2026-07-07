# Local Web Access

This page documents how ralph-workflow mediates local web access during an unattended run.


Ralph Workflow supports three related web capabilities: **search**, **visit**, and **crawl**. They solve different problems, so the fastest way to stay productive is to pick the smallest tool that fits the job.

## Search vs. visit vs. crawl

| Need | Tool | What you get |
|---|---|---|
| Find relevant pages | `web_search` | Titles, URLs, and snippets |
| Read one page | `visit_url` | Extracted readable text from a single URL |
| Traverse many pages or scrape a JS-heavy site | upstream crawler | Multi-page traversal and structured extraction |

## The default choice: `visit_url`

`visit_url` is Ralph Workflow's built-in page reader. It fetches a single URL and returns readable extracted text without any extra setup.

Use it when you want to:

- read one documentation page
- inspect a changelog, release note, or issue
- follow up on a URL returned by `web_search`

It ships with Ralph Workflow and works out of the box.

## When to use an upstream crawler

For multi-page crawls, JavaScript-rendered SPAs, or structured extraction, Ralph Workflow can delegate to a local upstream MCP server.

Use an upstream crawler when you need to:

- crawl an entire docs site
- scrape a JavaScript-heavy application
- extract structured content with selectors or schemas

This path requires extra configuration in `.agent/mcp.toml` because the crawler runs locally as a separate service.

## Choosing between them

| Need | Tool |
|---|---|
| Fetch one static HTML page | `visit_url` |
| JavaScript-rendered SPA | `ralph_upstream__crawl4ai__crawl` |
| Multi-page crawl | `ralph_upstream__crawl4ai__crawl_many` |
| Structured extraction | `ralph_upstream__crawl4ai__crawl` with an extraction schema |

## Safety posture

### SSRF guard

The built-in `visit_url` tool blocks requests to loopback, private-network, link-local, multicast, and other reserved address ranges by default. That means it will reject `localhost`, `127.0.0.1`, and private IPs unless you explicitly relax the guard.

This is intentional: it reduces the risk of exposing internal services by accident.

### When to enable private-network access

Set `allow_private_networks = true` in `[web_visit]` only when you understand the trade-off and the environment is appropriately isolated.

Typical cases:

- Ralph Workflow runs in an isolated container or VM
- you intentionally want access to local development servers
- a CI runner has its own dedicated network boundary

### Timeouts and size limits

`visit_url` enforces:

- a 15-second default timeout per request
- a 2 MiB maximum response body size

These limits keep fetch operations from growing into unbounded background work.

## Tool names you will see

The most important names are:

- `web_search` — search the web
- `visit_url` — read one page
- `ralph_upstream__crawl4ai__crawl` — crawl through an upstream crawler when configured
- `ralph_upstream__crawl4ai__crawl_many` — batch crawling through that upstream crawler

## Related pages

- [MCP Tools Reference](mcp-tools.md) — broader tool surface and capability gates
- [Concepts](concepts.md) — MCP and capability terminology
- [Configuration](configuration.md) — enabling `web.search` and `web.visit`
