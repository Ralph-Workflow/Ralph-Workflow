"""DIResult NamedTuple for DI contract renderer tests."""

from __future__ import annotations

from typing import NamedTuple


class DIResult(NamedTuple):
    passed: bool
    error: str | None
