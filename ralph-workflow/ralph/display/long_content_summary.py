"""Opt-in summary layer for oversized agent content blocks.

When RALPH_LONG_CONTENT_SUMMARY=1 (or 'true'/'yes') is set and the content
exceeds 4000 display cells, a deterministic headline summary is extracted
from the already-AI-produced text. This is additive: head+tail condensation
remains the trusted default; the summary is an extra layer for quick context.

Set RALPH_LONG_CONTENT_SUMMARY=1 to enable. The summary is derived from the
first non-empty line of the content (markdown headings stripped), capped at
120 characters. No external AI call is made — the upstream provider already
produced the content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len

if TYPE_CHECKING:
    from collections.abc import Mapping

_SUMMARY_THRESHOLD = 4000
_SUMMARY_ENV_VALUES = frozenset({"1", "true", "yes"})


def should_summarize(text: str, env: Mapping[str, str]) -> bool:
    """Return True when the env flag is set AND text exceeds the threshold."""
    flag = env.get("RALPH_LONG_CONTENT_SUMMARY", "")
    if flag not in _SUMMARY_ENV_VALUES:
        return False
    try:
        return cell_len(text) > _SUMMARY_THRESHOLD
    except Exception:
        return False


def build_headline_summary(text: str, max_chars: int = 120) -> str:
    """Extract the first non-empty line, strip markdown heading/quote prefixes, truncate."""
    for line in text.splitlines():
        stripped = line.lstrip("#> ").strip()
        if stripped:
            if len(stripped) <= max_chars:
                return stripped
            return stripped[:max_chars] + "…"
    return ""


__all__ = ["build_headline_summary", "should_summarize"]
