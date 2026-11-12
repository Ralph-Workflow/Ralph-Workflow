from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def agent_environment_value(name: str) -> str | None:
    return os.environ.get(name)


def opencode_binary_override(env_getter: Callable[[str], str | None] | None = None) -> str | None:
    getter = env_getter if env_getter is not None else os.environ.get
    return getter("RALPH_OPENCODE_BINARY")
