from __future__ import annotations

from ralph.agents.invoke._completion import completion_run_id_from_extra_env
from ralph.mcp.protocol.env import MCP_RUN_ID_ENV


def test_returns_run_id_from_env() -> None:
    assert completion_run_id_from_extra_env({str(MCP_RUN_ID_ENV): "run-9"}) == "run-9"


def test_none_when_env_absent() -> None:
    assert completion_run_id_from_extra_env(None) is None
    assert completion_run_id_from_extra_env({}) is None
