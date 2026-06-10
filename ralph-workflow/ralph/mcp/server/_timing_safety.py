"""End-to-end timing safety invariant for the production MCP transport.

The relationship that keeps the system safe is: the server's worst-case
resolution time is strictly less than the client's request timeout. This
is a guarded, tested property — the sum of the three bounded constants
(DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS) must
be < CLIENT_REQUEST_TIMEOUT_MS, both at import time and at runtime.

The invariant is enforced via ``if``/``raise RuntimeError`` (NOT
``assert``) so ``python -O`` cannot strip it. The constants themselves
are imported from :mod:`ralph.timeout_defaults` so the production and
test layers cannot drift.
"""

from __future__ import annotations

import os

from ralph.mcp.protocol.env import MCP_REQUEST_TIMEOUT_MS_ENV
from ralph.timeout_defaults import (
    EXEC_MAX_TIMEOUT_MS,
    KILL_ESCALATION_CEILING_MS,
    SSE_DRAIN_CEILING_MS,
)

#: Default client request timeout (milliseconds). 330_000 = 5 minutes +
#: 30 seconds of margin over the server's worst-case. Override via
#: RALPH_MCP_REQUEST_TIMEOUT_MS.
DEFAULT_CLIENT_REQUEST_TIMEOUT_MS: int = 330_000

#: Effective client request timeout (milliseconds). Read from
#: RALPH_MCP_REQUEST_TIMEOUT_MS at import time; falls back to the
#: built-in default.
CLIENT_REQUEST_TIMEOUT_MS: int = int(
    os.environ.get(MCP_REQUEST_TIMEOUT_MS_ENV, str(DEFAULT_CLIENT_REQUEST_TIMEOUT_MS))
)

#: Worst-case dispatch cap. Equal to EXEC_MAX_TIMEOUT_MS — the longest a
#: single exec call may run.
DISPATCH_CAP_MS: int = EXEC_MAX_TIMEOUT_MS

#: Worst-case SSE drain ceiling. The post-final-frame grace the server
#: gives the connection to flush its last write.
DRAIN_CEILING_MS: int = SSE_DRAIN_CEILING_MS

#: Worst-case kill escalation ceiling. The SIGTERM-then-SIGKILL grace
#: before a child is hard-killed.
KILL_ESCALATION_CEILING_MS: int = KILL_ESCALATION_CEILING_MS  # noqa: PLW0127

#: End-to-end timing safety margin. The server's worst case (sum of the
#: three constants above) must remain strictly less than the client's
#: request timeout — otherwise a slow client can hit its request
#: timeout while the server is still dispatching, producing the
#: ``-32001`` retry storm the architecture is built to prevent.
SERVER_WORST_CASE_MS: int = DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS

# Import-time invariant (NOT assert) — survives python -O.
if not SERVER_WORST_CASE_MS < CLIENT_REQUEST_TIMEOUT_MS:
    raise RuntimeError(
        f"dispatch_cap exceeds client_request_timeout: "
        f"SERVER_WORST_CASE_MS={SERVER_WORST_CASE_MS} "
        f"({DISPATCH_CAP_MS} dispatch + {DRAIN_CEILING_MS} drain "
        f"+ {KILL_ESCALATION_CEILING_MS} kill) "
        f">= CLIENT_REQUEST_TIMEOUT_MS={CLIENT_REQUEST_TIMEOUT_MS}"
    )


def timing_safety_margin_ms() -> int:
    """Return the safety margin in milliseconds (client timeout minus server worst case)."""
    return CLIENT_REQUEST_TIMEOUT_MS - SERVER_WORST_CASE_MS


__all__ = [
    "CLIENT_REQUEST_TIMEOUT_MS",
    "DEFAULT_CLIENT_REQUEST_TIMEOUT_MS",
    "DISPATCH_CAP_MS",
    "DRAIN_CEILING_MS",
    "KILL_ESCALATION_CEILING_MS",
    "SERVER_WORST_CASE_MS",
    "timing_safety_margin_ms",
]
