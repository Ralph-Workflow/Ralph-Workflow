"""HTML text extraction for the visit_url tool.

Uses readability-lxml for main-content isolation and selectolax for fast
plain-text rendering. Both dependencies are optional ([web-visit] extras).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urljoin, urlparse

_MAX_LINKS = 100


class _ReadabilityDocumentProtocol(Protocol):
    def __init__(self, input: str) -> None: ...
    def title(self) -> str: ...
    def summary(self, html_partial: bool = ...) -> str: ...


class _HTMLNodeProtocol(Protocol):
    @property
    def attributes(self) -> dict[str, str | None]: ...
    def decompose(self) -> None: ...
    def text(self, *, separator: str = ..., strip: bool = ...) -> str: ...


class _HTMLParserProtocol(Protocol):
    def __init__(self, html: str) -> None: ...
    @property
    def root(self) -> _HTMLNodeProtocol | None: ...
    def css(self, selector: str) -> list[_HTMLNodeProtocol]: ...


# Module-level optional imports — runtime may lack the extras.
_ReadabilityDocument: type[_ReadabilityDocumentProtocol] | None = None
_HTMLParser: type[_HTMLParserProtocol] | None = None
try:
    from readability import Document as _RawDocument  # noqa: I001  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    from selectolax.parser import HTMLParser as _RawHTMLParser

    _ReadabilityDocument = cast("type[_ReadabilityDocumentProtocol]", _RawDocument)
    _HTMLParser = cast("type[_HTMLParserProtocol]", _RawHTMLParser)
except ImportError:
    pass


@dataclass(frozen=True)
class ExtractedPage:
    """Result of extracting readable content from an HTML page."""

    title: str | None
    text: str
    links: tuple[str, ...]


def _require_deps() -> tuple[type[_ReadabilityDocumentProtocol], type[_HTMLParserProtocol]]:
    if _ReadabilityDocument is None or _HTMLParser is None:
        raise ImportError(
            "Web visit text extraction requires optional dependencies. "
            "Install them with: pip install ralph-workflow[web-visit]"
        )
    return _ReadabilityDocument, _HTMLParser


def extract_readable(
    html: str,
    *,
    base_url: str | None,
    with_links: bool,
) -> ExtractedPage:
    """Extract readable text and optional links from raw HTML.

    Requires the [web-visit] extras: pip install ralph-workflow[web-visit]
    """
    document_cls, html_parser_cls = _require_deps()

    doc = document_cls(html)
    title: str | None = doc.title() or None
    content_html = doc.summary(html_partial=True)

    parser = html_parser_cls(content_html)
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
    if _HTMLParser is None:
        return ()

    parser = _HTMLParser(html)
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
