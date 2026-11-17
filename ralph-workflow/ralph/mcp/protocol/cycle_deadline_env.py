from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import CYCLE_WARN_EPOCH_ENV, DEV_WARN_EPOCH_ENV

if TYPE_CHECKING:
    from ralph.mcp.websearch.secrets import EnvGetter

__all__ = [
    "cycle_warning_is_active",
    "development_warning_is_active",
    "read_published_epoch",
]


def read_published_epoch(name: str, env_getter: EnvGetter = os.environ.get) -> float | None:
    raw = env_getter(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def development_warning_is_active(
    *,
    now_epoch: float,
    env_getter: EnvGetter = os.environ.get,
) -> bool:
    warn_epoch = read_published_epoch(DEV_WARN_EPOCH_ENV, env_getter)
    return warn_epoch is not None and now_epoch >= warn_epoch


def cycle_warning_is_active(
    *,
    now_epoch: float,
    env_getter: EnvGetter = os.environ.get,
) -> bool:
    warn_epoch = read_published_epoch(CYCLE_WARN_EPOCH_ENV, env_getter)
    return warn_epoch is not None and now_epoch >= warn_epoch
