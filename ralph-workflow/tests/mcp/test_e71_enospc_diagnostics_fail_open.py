"""Tests for E7.1: ENOSPC fail-open diagnostics and fail-closed ledger.

Product Criteria E7.1:
- Disk full (ENOSPC) or read-only .agent/: stage files, the server log, the durable
  sink and the ledger can fail to write.
- Required: diagnostics fail open (startup and serving continue; the stage reads
  as "unknown"), while integrity stores such as the ledger fail closed with a
  typed error (WireLedgerIOError).
- Neither path may crash with an untyped error.
"""

from __future__ import annotations

import errno
import uuid
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._pre_serve_overrun import (
    PRE_SERVE_OVERRUN_EXIT_CODE,
    fail_closed_pre_serve_overrun,
)
from ralph.mcp.server._start_stage_tracker import (
    read_last_stage,
    record_overrun,
    stage_file_path,
)
from ralph.mcp.server._wire_ledger import (
    WireLedgerIOError,
    append_wire_record,
)


class _ExitInterceptedError(Exception):
    """Sentinel exception raised by mock os._exit to halt execution in test."""

    def __init__(self, code: int) -> None:
        super().__init__(f"os._exit({code})")
        self.code = code


def _mock_exit(code: int) -> NoReturn:
    raise _ExitInterceptedError(code)


def test_e71_enospc_overrun_diagnostics_fail_open(tmp_path: Path) -> None:
    """Overrun diagnostics logging fails open on ENOSPC without untyped crash.

    When writing the overrun diagnostics JSONL log encounters ENOSPC,
    record_overrun must swallow the write error and not propagate it to the caller.
    """
    sid = str(uuid.uuid4())
    p = stage_file_path(tmp_path, spawn_id=sid)
    overruns_path = Path(str(p) + ".overruns")

    with patch.object(
        Path,
        "open",
        side_effect=OSError(errno.ENOSPC, "No space left on device"),
    ):
        # Must not raise OSError(ENOSPC)
        record_overrun(
            tmp_path,
            stage="config_loading",
            elapsed_s=1.5,
            budget_s=1.0,
            remaining_s=0.0,
            headroom_s=-0.5,
            timeout_s=0.1,
        )

    # Overruns file was not created due to simulated ENOSPC, but execution continued
    assert not overruns_path.exists()


def test_e71_enospc_overrun_flush_diagnostics_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overrun flush diagnostics suppresses ENOSPC and terminates with typed exit code.

    During fail_closed_pre_serve_overrun, diagnostics flushing must fail open
    even if the disk is completely full, completing within the diagnostic timeout
    and exiting with the stable overrun exit code.
    """
    monkeypatch.setattr("os._exit", _mock_exit)

    with patch(
        "ralph.mcp.server._pre_serve_overrun._record_stage",
        side_effect=OSError(errno.ENOSPC, "No space left on device"),
    ):
        with pytest.raises(_ExitInterceptedError) as exc_info:
            fail_closed_pre_serve_overrun(
                tmp_path,
                stage="config_loading",
                elapsed_s=2.5,
                budget_s=1.0,
                exit_code=PRE_SERVE_OVERRUN_EXIT_CODE,
                diagnostic_timeout_s=0.5,
            )

        assert exc_info.value.code == PRE_SERVE_OVERRUN_EXIT_CODE


def test_e71_read_last_stage_enospc_returns_none(tmp_path: Path) -> None:
    """read_last_stage returns None on ENOSPC rather than raising untyped error.

    When read_last_stage encounters an OSError (such as ENOSPC or EIO) trying to read
    the stage file, it fails open by returning None.
    """
    sid = str(uuid.uuid4())

    with patch.object(
        Path,
        "read_text",
        side_effect=OSError(errno.ENOSPC, "No space left on device"),
    ):
        stage = read_last_stage(tmp_path, spawn_id=sid)
        assert stage is None


def test_e71_stage_marked_unknown_when_stage_file_missing_or_failed() -> None:
    """When the stage file could not be written, the stage reads as 'last_stage_unknown'.

    The supervisor/bridge error contract converts a None stage reading to the
    stable token 'last_stage_unknown'.
    """
    raw_stage: str | None = None
    last_stage_str: str = raw_stage if raw_stage is not None else "last_stage_unknown"

    err = McpServerError(
        "MCP server failed startup under disk exhaustion",
        restart_count=0,
        last_stage=last_stage_str,
    )
    assert err.last_stage == "last_stage_unknown"
    assert "last_stage=last_stage_unknown" in str(err)


def test_e71_wire_ledger_enospc_fails_closed_typed(tmp_path: Path) -> None:
    """Wire ledger fails closed with typed WireLedgerIOError on ENOSPC.

    Unlike diagnostics which fail open, integrity stores must fail closed with a
    typed error rather than allowing untyped crashes or silent data loss.
    """
    secret = "test-secret-e71"
    with (
        patch("fcntl.flock", side_effect=OSError(errno.ENOSPC, "No space left on device")),
        pytest.raises(WireLedgerIOError, match="wire_ledger_io_error") as exc_info,
    ):
        append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="test_tool",
            params={"path": "foo.txt"},
            run_id="run-e71",
            secret=secret,
        )

    # Verify error is typed and carries the underlying reason
    assert isinstance(exc_info.value, WireLedgerIOError)
    assert not isinstance(exc_info.value, OSError)
