from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config._env import getenv

if TYPE_CHECKING:
    from collections.abc import Callable


def agent_environment_value(name: str) -> str | None:
    return getenv(name)


def opencode_binary_override(env_getter: Callable[[str], str | None] | None = None) -> str | None:
    getter = env_getter if env_getter is not None else getenv
    return getter("RALPH_OPENCODE_BINARY")
