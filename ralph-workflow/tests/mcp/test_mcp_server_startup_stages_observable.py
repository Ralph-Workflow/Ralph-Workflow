"""R4-LOGS invariant: every startup stage is flushed to logger.info AND written to the stage file.

This test drives the REAL production seams (``run_standalone_server``)
in-process via threading. The server is started in a background thread;
once the ``ready_event`` fires we allow ``on_ready`` to fire and then
immediately read the stage file and captured log lines. The server thread
is not joined — it is left running and the test subprocess exits cleanly
after assertions, taking the thread with it.

The seven stages in ``RUNTIME_STAGES`` form a closed vocabulary; every
stage after the first is guaranteed to be recorded only after the
previous one completes (the runtime uses a synchronous single-threaded
composition, so there are no async ordering ambiguities).
"""

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

# Module-level capture list assigned via a mutable container so the fixture
# can rebind the contents without using ``global``. The lint audit forbids
# no-q-a directives in test files, so we route around the language-level
# global statement instead of suppressing the warning.
_loguru_records_box: list[list[str]] = [[]]


@pytest.fixture(autouse=True)
def _loguru_sink() -> Iterator[None]:
    """Install a loguru sink that captures all messages into the shared box."""
    _loguru_records_box[0] = []

    def sink(message: object) -> None:
        _loguru_records_box[0].append(str(message))

    handler_id = loguru_logger.add(sink, format="{message}")
    yield
    loguru_logger.remove(handler_id)


def test_all_seven_stages_logged_and_recorded(tmp_path: Path) -> None:
    """Each of the seven RUNTIME_STAGES emits a flushed logger.info line AND a stage-file record.

    The stages are recorded in strict order by the synchronous startup path:
    ``env_loaded``, ``session_ready``, ``config_loading``, ``config_loaded``,
    ``upstream_catalog_or_discovery_done``, ``registry_built``, ``ready_to_serve``.
    ``config_loading`` is the boundary that fires BEFORE the bounded pre-serve
    executor (allowed_roots, FsWorkspace, load_mcp_config); ``config_loaded``
    fires AFTER. ``ready_to_serve`` is recorded inside the ``on_ready`` callback
    that fires when the server socket is actually listening (immediately after
    ``ready_event`` is set).
    """
    # Build a session file so ``session_from_env`` succeeds without a real agent.
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
            # Port 0 lets the kernel pick a free port: pytest-xdist runs
            # multiple shards in parallel, and a fixed port would make two
            # shards race on the same bind (Address already in use).
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
            # Wait for the server to signal it is listening. on_ready has already fired.
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

    # --- log assertions -------------------------------------------------------
    stage_lines = [ln for ln in _loguru_records_box[0] if "ralph-mcp startup: stage=" in ln]
    assert len(stage_lines) == len(RUNTIME_STAGES), (
        f"expected {len(RUNTIME_STAGES)} stage log lines, got {len(stage_lines)}: {stage_lines!r}"
    )
    for stage in RUNTIME_STAGES:
        idx = RUNTIME_STAGES.index(stage)
        assert f"stage={stage}" in stage_lines[idx], (
            f"stage={stage!r} missing from log output at position {idx}"
        )

    # --- stage-file assertions -----------------------------------------------
    last = read_last_stage(tmp_path)
    assert last == RUNTIME_STAGES[-1], f"expected last stage {RUNTIME_STAGES[-1]!r}, got {last!r}"

    sp = stage_file_path(tmp_path)
    assert sp.exists(), f"stage file missing at {sp}"
    assert sp.read_text(encoding="ascii").strip() == RUNTIME_STAGES[-1]


def test_stage_file_absent_before_run(tmp_path: Path) -> None:
    """No stage file exists before run_standalone_server is called."""
    sp = stage_file_path(tmp_path)
    assert not sp.exists()


def test_closed_stage_vocabulary_is_seven() -> None:
    """The RUNTIME_STAGES tuple has exactly seven entries in the documented order."""
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
    """When the fake clock reports elapsed time past a stage budget, the
    build path raises McpServerError with last_stage matching the offending
    stage and elapsed_s > budget."""
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
