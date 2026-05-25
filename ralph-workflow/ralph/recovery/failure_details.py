"""Shared helpers for extracting and matching rich failure details."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable


def failure_detail_parts(exc: BaseException | str) -> list[str]:
    """Return all textual detail surfaces associated with a failure."""
    if isinstance(exc, str):
        text = exc.strip()
        return [text] if text else []

    parts: list[str] = []
    message = str(exc).strip()
    if message:
        parts.append(message)

    stderr = cast("object", getattr(exc, "stderr", None))
    if isinstance(stderr, str):
        stderr_text = stderr.strip()
        if stderr_text and stderr_text not in parts:
            parts.append(stderr_text)

    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list):
        for item in parsed_output:
            item_text = str(item).strip()
            if item_text:
                parts.append(item_text)

    return parts


def contains_casefolded_marker(parts: Iterable[str], markers: Iterable[str]) -> bool:
    """Return True when any marker appears in any part, case-insensitively."""
    folded_markers = tuple(marker.casefold() for marker in markers)
    for part in parts:
        folded_part = part.casefold()
        if any(marker in folded_part for marker in folded_markers):
            return True
    return False


def has_stale_session_details(
    exc: BaseException | str,
    markers: Iterable[str],
) -> bool:
    """Return True when any failure surface carries a stale-session signal."""
    return contains_casefolded_marker(failure_detail_parts(exc), markers)


__all__ = [
    "contains_casefolded_marker",
    "failure_detail_parts",
    "has_stale_session_details",
]
