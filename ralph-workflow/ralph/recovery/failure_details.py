from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable


def failure_message_parts(exc: BaseException | str) -> list[str]:
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
    return parts


class FailureDetailParts(list[str]):
    provider_parts: tuple[str, ...]

    def __init__(self, parts: Iterable[str], provider_parts: Iterable[str]) -> None:
        super().__init__(parts)
        self.provider_parts = tuple(provider_parts)


def provider_message_parts(exc: BaseException | str) -> list[str]:
    if isinstance(exc, str):
        return failure_message_parts(exc)
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    carries_transcript = isinstance(parsed_output, list) and bool(parsed_output)
    stderr = cast("object", getattr(exc, "stderr", None))
    if isinstance(stderr, str) and stderr.strip():
        return [stderr.strip()]
    if carries_transcript:
        return []
    return failure_message_parts(exc)


def failure_detail_parts(exc: BaseException | str) -> FailureDetailParts:
    provider_parts = provider_message_parts(exc)
    if isinstance(exc, str):
        return FailureDetailParts(failure_message_parts(exc), provider_parts)

    parts = failure_message_parts(exc)
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list):
        for item in parsed_output:
            item_text = str(item).strip()
            if item_text:
                parts.append(item_text)

    return FailureDetailParts(parts, provider_parts)


def contains_casefolded_marker(parts: Iterable[str], markers: Iterable[str]) -> bool:
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
    return contains_casefolded_marker(failure_detail_parts(exc), markers)


__all__ = [
    "FailureDetailParts",
    "contains_casefolded_marker",
    "failure_detail_parts",
    "failure_message_parts",
    "has_stale_session_details",
    "provider_message_parts",
]
