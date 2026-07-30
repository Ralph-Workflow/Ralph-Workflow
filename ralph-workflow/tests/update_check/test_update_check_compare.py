"""Version comparison for the update nagger."""

from __future__ import annotations

import pytest

from ralph.update_check.compare import is_newer


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [
        ("0.8.24", "0.9.1", True),
        ("0.8.24", "0.8.25", True),
        ("0.9.0", "0.8.24", False),
        ("0.8.24", "0.8.24", False),
        # A pre-release is not "newer" than the matching final under PEP 440.
        ("0.9.0", "0.9.0rc1", False),
        # A newer pre-release over an older final is newer.
        ("0.8.0", "0.9.0rc1", True),
    ],
)
def test_is_newer_orders_versions(current: str, latest: str, expected: bool) -> None:
    assert is_newer(current, latest) is expected


@pytest.mark.parametrize(
    ("current", "latest"),
    [("not-a-version", "0.9.0"), ("0.8.0", "garbage"), ("", "")],
)
def test_is_newer_returns_false_on_unparseable_input(current: str, latest: str) -> None:
    assert is_newer(current, latest) is False
