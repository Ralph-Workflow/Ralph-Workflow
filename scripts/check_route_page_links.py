#!/usr/bin/env python3
"""Link checker for non-Sphinx route pages.

Validates links in:
- README.md
- START_HERE.md
- docs/README.md
- ralph-workflow/docs/README.md

For each page, parses three link forms:
- '[text](url)' standard links
- '<url>' angle-bracket autolinks
- bare URLs (e.g. https://codeberg.org/...)

Internal relative links must resolve to a file on disk.
External http(s) links are checked with HTTP HEAD (fall back to GET on 405).
A bounded 10-second per-request timeout applies to all external requests.

The two exclusions inherited from ralph-workflow/docs/sphinx/conf.py are
also honored here: 'http://PROMPT.md' and 'https://docs.claude.com/'.

Exits non-zero on any broken link with a clear per-file, per-line report.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, NamedTuple
from urllib.parse import urlparse

try:
    import urllib.request
except ImportError:
    urllib = None  # type: ignore[assignment]


EXTERNAL_LINK_TIMEOUT_SECONDS = 10.0
EXTERNAL_IGNORES = {"http://PROMPT.md", "https://docs.claude.com/"}

LINK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[([^\]]+)\]\(([^)]+)\)"),
    re.compile(r"<(https?://[^>]+)>"),
    re.compile(r"(?<![\(\"<])(https?://[^\s<>\"')\]]+)"),
)


class LinkHit(NamedTuple):
    line_no: int
    text: str
    url: str


def collect_links(text: str) -> Iterable[LinkHit]:
    seen: set[tuple[int, str]] = set()
    for pattern in LINK_PATTERNS:
        for match in pattern.finditer(text):
            url = match.group(match.lastindex or 2).rstrip(".,;:!?)")
            line_no = text.count("\n", 0, match.start()) + 1
            key = (line_no, url)
            if key in seen:
                continue
            seen.add(key)
            text_repr = match.group(1) if match.lastindex and match.lastindex >= 1 else url
            yield LinkHit(line_no=line_no, text=text_repr, url=url)


def is_external(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def check_internal(link_file: Path, url: str) -> str | None:
    if url.startswith(("/", "#")):
        return None
    target = (link_file.parent / url).resolve()
    if not target.exists():
        return f"broken internal link: {url} (resolves to {target})"
    return None


def check_external(url: str) -> str | None:
    if url in EXTERNAL_IGNORES:
        return None
    if urllib is None:
        return None
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=EXTERNAL_LINK_TIMEOUT_SECONDS) as resp:
            status = getattr(resp, "status", 200)
            if status >= 400:
                return f"HTTP {status} on HEAD {url}"
    except urllib.error.HTTPError as exc:
        if exc.code == 405:
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(
                    req, timeout=EXTERNAL_LINK_TIMEOUT_SECONDS
                ) as resp:
                    status = getattr(resp, "status", 200)
                    if status >= 400:
                        return f"HTTP {status} on GET fallback {url}"
            except Exception as get_exc:  # noqa: BLE001
                return f"GET fallback failed for {url}: {get_exc}"
        else:
            return f"HTTP {exc.code} on HEAD {url}"
    except Exception as exc:  # noqa: BLE001
        return f"request failed for {url}: {exc}"
    return None


def check_file(path: Path) -> list[str]:
    if not path.exists():
        return [f"missing route file: {path}"]
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    for hit in collect_links(text):
        url = hit.url
        if is_external(url):
            err = check_external(url)
        else:
            err = check_internal(path, url)
        if err is not None:
            errors.append(f"{path}:{hit.line_no}: {err} (link text: {hit.text!r})")
    return errors


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_route_page_links.py FILE [FILE ...]", file=sys.stderr)
        return 2
    root = Path.cwd()
    all_errors: list[str] = []
    for raw in argv:
        path = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        all_errors.extend(check_file(path))
    if all_errors:
        print("route-page linkcheck FAILED:", file=sys.stderr)
        for line in all_errors:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"route-page linkcheck OK ({len(argv)} file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))