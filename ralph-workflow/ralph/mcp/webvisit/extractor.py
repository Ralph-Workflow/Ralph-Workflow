"""HTML text extraction for the visit_url tool.

Uses readability-lxml for main-content isolation and selectolax for fast
plain-text rendering. Both dependencies are included in the default ralph-workflow installation.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from readability import Document

_MAX_LINKS = 100


@dataclass(frozen=True)
class ExtractedPage:
    """Result of extracting readable content from an HTML page."""

    title: str | None
    text: str
    links: tuple[str, ...]


def extract_readable(
    html: str,
    *,
    base_url: str | None,
    with_links: bool,
) -> ExtractedPage:
    """Extract readable text and optional links from raw HTML."""
    doc = Document(html)
    title: str | None = doc.title() or None
    content_html = doc.summary(html_partial=True)

    parser = HTMLParser(content_html)
    for tag in parser.css("script, style, nav, footer, header, aside"):
        tag.decompose()

    raw_text = parser.root.text(separator="\n", strip=True) if parser.root else ""
    text = _collapse_whitespace(raw_text)

    links: tuple[str, ...] = ()
    if with_links:
        links = _extract_links(html, base_url=base_url)

    return ExtractedPage(title=title, text=text, links=links)


def _collapse_whitespace(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    non_empty: list[str] = []
    for line in lines:
        if line or (non_empty and non_empty[-1]):
            non_empty.append(line)
    return "\n".join(non_empty).strip()


def _extract_links(html: str, *, base_url: str | None) -> tuple[str, ...]:
    parser = HTMLParser(html)
    seen: set[str] = set()
    result: list[str] = []
    for node in parser.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url or "", href) if base_url else href
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if absolute not in seen:
            seen.add(absolute)
            result.append(absolute)
        if len(result) >= _MAX_LINKS:
            break
    return tuple(result)


__all__ = ["ExtractedPage", "extract_readable"]
