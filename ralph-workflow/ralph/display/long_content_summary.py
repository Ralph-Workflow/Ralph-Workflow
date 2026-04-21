"""Default-on summary layer for oversized agent content blocks.

When content exceeds 4000 display cells a deterministic headline summary is
extracted from the already-AI-produced text. This is additive: head+tail
condensation remains the trusted default; the summary is an extra layer for
quick context.

Set RALPH_LONG_CONTENT_SUMMARY=0 (or 'false'/'no'/'off') to disable.
When unset or empty the summary is enabled for content above the threshold.
The summary is derived from the first sentence of the content (markdown
headings stripped), capped at 120 characters. No external AI call is made.

Note: the sentence splitter is intentionally simple — it may truncate on
abbreviations ('e.g.') or URLs. The summary is additive and labelled
'↳ summary:' so a wrong headline never obscures the raw content.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.cells import cell_len

if TYPE_CHECKING:
    from collections.abc import Mapping

_SUMMARY_THRESHOLD = 4000
_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_ENABLED_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})

_SENTENCE_END = re.compile(r"[.!?\n]")


def should_summarize(text: str, env: Mapping[str, str]) -> bool:
    """Return True when text exceeds the threshold and the kill-switch is not set."""
    flag = env.get("RALPH_LONG_CONTENT_SUMMARY", "").lower().strip()
    if flag in _DISABLED_VALUES:
        return False
    try:
        return cell_len(text) > _SUMMARY_THRESHOLD
    except Exception:
        return False


def build_content_summary(text: str, max_chars: int = 200) -> str:
    """Extract the first sentence, strip markdown prefixes, truncate to max_chars.

    Falls back to the first non-empty line when no sentence terminator is found.
    Returns '' when no non-empty line exists.
    """
    for line in text.splitlines():
        stripped = line.lstrip("#> ").strip()
        if not stripped:
            continue
        m = _SENTENCE_END.search(stripped)
        if m:
            candidate = stripped[: m.end()].strip()
            if candidate:
                if len(candidate) <= max_chars:
                    return candidate
                return candidate[:max_chars] + "…"
        if len(stripped) <= max_chars:
            return stripped
        return stripped[:max_chars] + "…"
    return ""


def build_headline_summary(text: str, max_chars: int = 120) -> str:
    """Extract a short headline from text, capped at max_chars."""
    return build_content_summary(text, max_chars=max_chars)


__all__ = ["build_content_summary", "build_headline_summary", "should_summarize"]
