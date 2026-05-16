"""Shared signal handler typing helpers."""

from __future__ import annotations

from collections.abc import Callable
from types import FrameType

type SignalHandler = Callable[[int, FrameType | None], object] | int | None


__all__ = ["SignalHandler"]
