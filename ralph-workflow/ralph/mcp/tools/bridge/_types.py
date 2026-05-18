"""Shared type aliases for the bridge package."""

from __future__ import annotations

from collections.abc import Callable

JsonObject = dict[str, object]
ToolHandler = Callable[[object, object, JsonObject], object]
