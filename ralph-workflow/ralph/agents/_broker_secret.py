"""The broker-owned secret shared between a Ralph parent and its children."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Name of the environment variable that carries the broker-owned secret.
#: The VALUE is the variable's NAME, never a secret: the secret itself is
#: read from the environment at call time.
_BROKER_ENV_VAR_NAME = "RALPH_BROKER_SECRET"


def _parent_broker_secret(getenv: Callable[[str], str | None] = os.getenv) -> str | None:
    """Return the parent-only broker secret used for completion verification."""
    return getenv(_BROKER_ENV_VAR_NAME)
