"""Web visit capability: single-page URL fetch and readable text extraction.

This package implements the ``visit_url`` MCP tool that agents use to fetch and read
web pages. It fetches a single URL over HTTP/HTTPS, extracts readable text from the
HTML, and returns the content as Markdown-like plain text.

Main entry points:

- ``ralph.mcp.webvisit.fetcher`` — HTTP fetch layer; sends the request, follows
  redirects, and enforces the allowed-scheme/SSRF guard.
- ``ralph.mcp.webvisit.extractor`` — HTML-to-text extraction; strips scripts, styles,
  and navigation boilerplate and returns readable body text.

This package is a pure back-end implementation; the MCP tool registration lives in
``ralph.mcp.tools.webvisit``. It does not support multi-page crawling; for that, see
the upstream Crawl4AI/Firecrawl configuration described in the Local Web Access docs.
"""
