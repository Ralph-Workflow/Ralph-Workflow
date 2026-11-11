
from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def require_trust_boundary(
    auth_header: str | None,
    env: Mapping[str, str],
) -> None:
    expected = env.get("MCP_AUTH_TOKEN", "")
    if not expected:
        return
    if auth_header is None:
        raise PermissionError("missing Authorization header")
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        raise PermissionError("Authorization scheme must be Bearer")
    presented = auth_header[len(prefix) :]
    expected_bytes = expected.encode("utf-8")
    presented_bytes = presented.encode("utf-8")
    if not hmac.compare_digest(expected_bytes, presented_bytes):
        raise PermissionError("token mismatch")


__all__ = ["require_trust_boundary"]
