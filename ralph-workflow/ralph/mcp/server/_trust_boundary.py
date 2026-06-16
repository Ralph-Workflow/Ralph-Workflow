"""Exec surface trust boundary for the production MCP HTTP transport.

The exec surface is POST /mcp with tools/call to exec or unsafe_exec.
The trust boundary is explicit and enforced:

1. The bind host MUST be 127.0.0.1 (never 0.0.0.0).
2. Optional token check via Authorization: Bearer <token> when MCP_AUTH_TOKEN
   env is set.

The token comparison uses ``hmac.compare_digest`` (NEVER ``==``) to avoid
timing-side-channel attacks, and tokens are normalized to bytes (utf-8)
before comparison so str/bytes mismatches cannot bypass the check.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def require_trust_boundary(
    auth_header: str | None,
    env: Mapping[str, str],
) -> None:
    """Enforce the trust boundary. Raises ``PermissionError`` on failure.

    Args:
        auth_header: The value of the ``Authorization`` header (or None if
            missing).
        env: A mapping (typically ``os.environ`` or a test substitute) from
            which to read ``MCP_AUTH_TOKEN``.

    Raises:
        PermissionError: when the token check fails (or the header is
            missing and a token is configured).
    """
    expected = env.get("MCP_AUTH_TOKEN", "")
    # Empty / unset token → no-op (the executor is bound to 127.0.0.1).
    if not expected:
        return
    if auth_header is None:
        raise PermissionError("missing Authorization header")
    # Expect "Bearer <token>"; reject anything else.
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        raise PermissionError("Authorization scheme must be Bearer")
    presented = auth_header[len(prefix) :]
    # Constant-time compare on bytes; hmac.compare_digest requires equal-length
    # str or bytes; we coerce both to bytes (utf-8) for the comparison.
    expected_bytes = expected.encode("utf-8")
    presented_bytes = presented.encode("utf-8")
    if not hmac.compare_digest(expected_bytes, presented_bytes):
        raise PermissionError("token mismatch")


__all__ = ["require_trust_boundary"]
