"""R4-LOGS invariant: every startup stage is flushed to logger.info AND written to the stage file.

This test drives the REAL production seams (``run_standalone_server``)
in-process via threading. The server is started in a background thread;
once the ``ready_event`` fires we allow ``on_ready`` to fire and then
immediately read the stage file and captured log lines. The server thread
is not joined — it is left running and the test subprocess exits cleanly
after assertions, taking the thread with it.

The six stages in ``RUNTIME_STAGES`` form a closed vocabulary; every
stage after the first is guaranteed to be recorded only after the
previous one completes (the runtime uses a synchronous single-threaded
composition, so there are no async ordering ambiguities).
"""

from __future__ import annotations

import threading
import time as time_module
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

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

_loguru_records: list[str] = []


@pytest.fixture(autouse=True)
def _loguru_sink() -> Iterator[None]:
    """Install a loguru sink that captures all messages to _loguru_records."""
    global _loguru_records  # noqa: PLW0603 — module-level capture list is the fixture's contract
    _loguru_records = []

    def sink(message: object) -> None:
        _loguru_records.append(str(message))

    handler_id = loguru_logger.add(sink, format="{message}")
    yield
    loguru_logger.remove(handler_id)


def test_all_six_stages_logged_and_recorded(tmp_path: Path) -> None:
    """Each of the six RUNTIME_STAGES emits a flushed logger.info line AND a stage-file record.

    The stages are recorded in strict order by the synchronous startup path:
    ``env_loaded``, ``session_ready``, ``config_loaded``,
    ``upstream_catalog_or_discovery_done``, ``registry_built``, ``ready_to_serve``.
    ``ready_to_serve`` is recorded inside the ``on_ready`` callback that fires
    when the server socket is actually listening (immediately after ``ready_event`` is set).
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

        def run_server() -> None:
            run_standalone_server(tmp_path, ready_event=ready_event)

        server_thread = threading.Thread(target=run_server, name="mcp-test-server")
        server_thread.start()

        try:
            # Wait for the server to signal it is listening, then give
            # ``on_ready`` a moment to fire and record ``ready_to_serve``.
            assert ready_event.wait(timeout=10.0), "server did not signal ready within 10s"
            # Brief grace period for on_ready → _record_ready_to_serve to execute.
            time_module.sleep(0.3)
        finally:
            # Do NOT join the server thread — it blocks in server.run() forever.
            # The test subprocess exits after assertions and takes the thread with it.
            pass

    finally:
        if old_session_file is not None:
            os.environ["RALPH_MCP_SESSION_FILE"] = old_session_file
        elif "RALPH_MCP_SESSION_FILE" in os.environ:
            del os.environ["RALPH_MCP_SESSION_FILE"]

    # --- log assertions -------------------------------------------------------
    stage_lines = [ln for ln in _loguru_records if "ralph-mcp startup: stage=" in ln]
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


def test_closed_stage_vocabulary_is_six() -> None:
    """The RUNTIME_STAGES tuple has exactly six entries in the documented order."""
    assert len(RUNTIME_STAGES) == 6
    assert RUNTIME_STAGES == (
        "env_loaded",
        "session_ready",
        "config_loaded",
        "upstream_catalog_or_discovery_done",
        "registry_built",
        "ready_to_serve",
    )
