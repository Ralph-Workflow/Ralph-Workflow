from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_BROKER_ENV_VAR_NAME = "RALPH_BROKER_SECRET"


def parent_broker_secret(getenv: Callable[[str], str | None] = os.getenv) -> str | None:
    return getenv(_BROKER_ENV_VAR_NAME)
