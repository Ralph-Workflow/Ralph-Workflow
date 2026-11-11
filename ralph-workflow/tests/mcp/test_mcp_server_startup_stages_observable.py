from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._start_stage_tracker import (
    RUNTIME_STAGES,
    read_last_stage,
    stage_file_path,
)
from ralph.mcp.server.lifecycle import session_payload_json
from ralph.mcp.server.runtime import run_standalone_server

if TYPE_CHECKING:
    from collections.abc import Iterator

_loguru_records_box: list[list[str]] = [[]]


@pytest.fixture(autouse=True)
def _loguru_sink() -> Iterator[None]:
    _loguru_records_box[0] = []

    def sink(message: object) -> None:
        _loguru_records_box[0].append(str(message))

    handler_id = loguru_logger.add(sink, format="{message}")
    yield
    loguru_logger.remove(handler_id)


@pytest.mark.timeout_seconds(10)
def test_all_seven_stages_logged_and_recorded(tmp_path: Path) -> None:
    session = AgentSession(
        session_id="test-session",
        run_id="test-run",
        drain="test",
        capabilities={cap.value for cap in Capability},
    )
    session_file = tmp_path / "session.json"
    session_file.write_text(session_payload_json(session), encoding="utf-8")

    import os

    old_session_file = os.environ.get("RALPH_MCP_SESSION_FILE")
    try:
        os.environ["RALPH_MCP_SESSION_FILE"] = str(session_file)

        ready_event = threading.Event()
        stop_event = threading.Event()

        def run_server() -> None:
            run_standalone_server(
                tmp_path,
                ready_event=ready_event,
                stop_event=stop_event,
                port=0,
            )

        server_thread = threading.Thread(
            target=run_server,
            name="mcp-test-server",
            daemon=True,
        )
        server_thread.start()

        try:
            assert ready_event.wait(timeout=10.0), "server did not signal ready within 10s"
        finally:
            stop_event.set()
            server_thread.join(timeout=5.0)
            assert not server_thread.is_alive(), "server thread did not shut down cleanly"

    finally:
        if old_session_file is not None:
            os.environ["RALPH_MCP_SESSION_FILE"] = old_session_file
        elif "RALPH_MCP_SESSION_FILE" in os.environ:
            del os.environ["RALPH_MCP_SESSION_FILE"]

    stage_lines = [ln for ln in _loguru_records_box[0] if "ralph-mcp startup: stage=" in ln]
    assert len(stage_lines) == len(RUNTIME_STAGES), (
        f"expected {len(RUNTIME_STAGES)} stage log lines, got {len(stage_lines)}: {stage_lines!r}"
    )
    for stage in RUNTIME_STAGES:
        idx = RUNTIME_STAGES.index(stage)
        assert f"stage={stage}" in stage_lines[idx], (
            f"stage={stage!r} missing from log output at position {idx}"
        )

    last = read_last_stage(tmp_path)
    assert last == RUNTIME_STAGES[-1], f"expected last stage {RUNTIME_STAGES[-1]!r}, got {last!r}"

    sp = stage_file_path(tmp_path)
    assert sp.exists(), f"stage file missing at {sp}"
    assert sp.read_text(encoding="ascii").strip() == RUNTIME_STAGES[-1]


def test_stage_file_absent_before_run(tmp_path: Path) -> None:
    sp = stage_file_path(tmp_path)
    assert not sp.exists()


def test_closed_stage_vocabulary_is_seven() -> None:
    assert len(RUNTIME_STAGES) == 7
    assert RUNTIME_STAGES == (
        "env_loaded",
        "session_ready",
        "config_loading",
        "config_loaded",
        "upstream_catalog_or_discovery_done",
        "registry_built",
        "ready_to_serve",
    )


def test_observed_stage_deadline_enforces_bound(tmp_path: Path) -> None:
    from unittest.mock import patch

    from ralph.mcp.protocol._startup_timeouts import mcp_discovery_budget_from_env
    from ralph.mcp.server import runtime as runtime_module
    from ralph.mcp.server._mcp_server_error import McpServerError
    from ralph.mcp.server.runtime import McpServerExtras

    budget = mcp_discovery_budget_from_env({}).total_seconds()
    past_budget = budget + 5.0
    ticks = iter((0.0, past_budget, past_budget * 2, past_budget * 3))
    session = AgentSession(
        session_id="test-session",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )
    with (
        patch.object(runtime_module, "_clock", lambda: next(ticks)),
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=(),
        ),
        pytest.raises(McpServerError) as exc_info,
    ):
        runtime_module.build_standalone_http_server(
            workspace_root=tmp_path,
            extras=McpServerExtras(
                session=session,
                upstream_registry=None,
                mcp_config=load_mcp_config(config_path=None),
            ),
            env={},
        )

    err = exc_info.value
    assert err.last_stage in (
        "config_loading",
        "config_loaded",
        "upstream_catalog_or_discovery_done",
    )
    assert err.elapsed_s is not None and err.elapsed_s > budget
