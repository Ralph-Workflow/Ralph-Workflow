"""Lifecycle/retry parser types must not render as bodiless WARN banners."""

from __future__ import annotations

import pytest

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_router import map_parser_type_to_kind


@pytest.mark.parametrize(
    ("parser_type", "expected"),
    [
        ("stop", ActivityEventKind.LIFECYCLE),
        ("session", ActivityEventKind.LIFECYCLE),
        ("auto_retry_start", ActivityEventKind.STATUS),
        ("auto_retry_end", ActivityEventKind.LIFECYCLE),
        ("agent_settled", ActivityEventKind.LIFECYCLE),
    ],
)
def test_lifecycle_types_are_not_unknown(parser_type: str, expected: ActivityEventKind) -> None:
    assert map_parser_type_to_kind(parser_type) is expected


def test_genuinely_unknown_type_still_maps_to_unknown() -> None:
    assert map_parser_type_to_kind("some_future_event") is ActivityEventKind.UNKNOWN
