"""Reading the cycle deadline the pipeline publishes into the environment.

The pipeline publishes a guarded cycle's warning point and deadline as
wall-clock epochs (see :class:`ralph.mcp.protocol.env.McpEnvVar`) so every
process taking part in an invocation — the pipeline itself and the MCP server
subprocess it spawns — reads the same instants. Both the tool-result nag and
artifact validation key off that publication, so the parsing lives here rather
than in either consumer.
"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import CYCLE_WARN_EPOCH_ENV

if TYPE_CHECKING:
    from ralph.mcp.websearch.secrets import EnvGetter

__all__ = ["cycle_warning_is_active", "read_published_epoch"]


def read_published_epoch(name: str, env_getter: EnvGetter = os.environ.get) -> float | None:
    """Parse a published epoch, treating anything unusable as "not published".

    Non-finite values are rejected alongside unparseable ones: a ``nan``
    reaching downstream arithmetic compares false against every bound, which
    would silently disable a deadline that was in fact published.
    """
    raw = env_getter(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def cycle_warning_is_active(
    *,
    now_epoch: float,
    env_getter: EnvGetter = os.environ.get,
) -> bool:
    """Return whether the running cycle has passed its warning point.

    This is the runtime's own answer to "was this agent warned", which is what
    artifact validation must gate on: an agent that reports its own warned
    state can clear a gate by staying silent. ``False`` when no deadline is
    published — a run without a cycle timebox, or work outside a guarded
    cycle — so the gate cannot fire where no warning could have been issued.
    """
    warn_epoch = read_published_epoch(CYCLE_WARN_EPOCH_ENV, env_getter)
    return warn_epoch is not None and now_epoch >= warn_epoch
