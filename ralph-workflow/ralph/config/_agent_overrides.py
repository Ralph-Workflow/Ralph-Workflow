"""Environment-backed agent binary override accessors."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def agent_environment_value(name: str) -> str | None:
    """Return an agent runtime environment value at the configuration boundary."""
    return os.environ.get(name)


def opencode_binary_override(env_getter: Callable[[str], str | None] | None = None) -> str | None:
    """Return the raw ``RALPH_OPENCODE_BINARY`` env value, if set."""
    getter = env_getter if env_getter is not None else os.environ.get
    return getter("RALPH_OPENCODE_BINARY")
