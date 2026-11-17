from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config._env import getenv as config_getenv

if TYPE_CHECKING:
    from collections.abc import Callable

_BROKER_ENV_VAR_NAME = "RALPH_BROKER_SECRET"


def parent_broker_secret(getenv: Callable[[str], str | None] = config_getenv) -> str | None:
    return getenv(_BROKER_ENV_VAR_NAME)
