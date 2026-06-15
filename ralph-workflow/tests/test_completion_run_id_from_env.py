"""The gate's run identity for a subprocess agent comes from MCP_RUN_ID_ENV.

The submission handler stamps receipts with the MCP session's run_id, and the
launcher sets MCP_RUN_ID_ENV to that exact run_id. Resolving the gate's run_id
from the same env var is what makes a receipt written by the handler visible to
the gate for subprocess (OpenCode) agents — without it, the gate falls back to
the transport session id and never finds the receipt.
"""

from __future__ import annotations

from ralph.agents.invoke._completion import completion_run_id_from_extra_env
from ralph.mcp.protocol.env import MCP_RUN_ID_ENV


def test_returns_run_id_from_env() -> None:
    assert completion_run_id_from_extra_env({str(MCP_RUN_ID_ENV): "run-9"}) == "run-9"


def test_none_when_env_absent() -> None:
    assert completion_run_id_from_extra_env(None) is None
    assert completion_run_id_from_extra_env({}) is None
