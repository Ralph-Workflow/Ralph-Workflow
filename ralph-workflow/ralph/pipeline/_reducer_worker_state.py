"""Deprecated: re-export of the fan-out authority handlers.

The fan-out authority now lives in
:mod:`ralph.pipeline._fan_out_authority`. This shim remains for
one release so existing imports keep working; new code MUST import
from the new module.
"""

from __future__ import annotations

import warnings

from ralph.pipeline._fan_out_authority import (
    handle_fan_out_started,
    handle_worker_completed,
    handle_worker_failed,
    handle_worker_started,
    handle_workers_resumed,
)

warnings.warn(
    "ralph.pipeline._reducer_worker_state is deprecated; "
    "import from ralph.pipeline._fan_out_authority instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "handle_fan_out_started",
    "handle_worker_completed",
    "handle_worker_failed",
    "handle_worker_started",
    "handle_workers_resumed",
]
