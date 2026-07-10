"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.tools.coordination import CapabilityDeniedError
from ralph.mcp.tools.git_read import (
    GIT_STATUS_READ_CAPABILITY,
    handle_git_status,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestHandleGitStatus:
    def test_status_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())  # No capabilities
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_status(session, workspace, {})

    def test_status_returns_output(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
            mock_git.return_value = "On branch main\nnothing to commit"
            result = handle_git_status(session, workspace, {})
            assert result.is_error is False
            assert "On branch main" in result.content[0].text

    def test_status_compact_emits_index_metadata_without_handle(
        self, tmp_path: Path
    ) -> None:
        """AC-06: compact ``git_status`` always surfaces
        ``index_used`` and ``index_status`` so agents do not
        guess when the index is unavailable.
        """
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        completed = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=b"M  a.py\n",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ):
            result = handle_git_status(
                session, workspace, {"format": "compact"}
            )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["format"] == "compact"
        assert "index_used" in payload
        assert "index_status" in payload
        assert "fallback_reason" in payload
        # No handle attached: index is unavailable, not used.
        assert payload["index_used"] is False
        assert payload["index_status"] == "unavailable"
        assert payload["index_generation"] == 0
        assert payload["changed_symbols"] == {}
        assert payload["fallback_reason"] == "index_not_attached"

    def test_status_compact_marks_index_stale_when_generation_zero(
        self, tmp_path: Path
    ) -> None:
        """AC-06: when the attached index has no committed
        generation, the compact payload marks the index as
        stale with an explicit reason so agents do not treat
        absent evidence as fresh.
        """
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        # Attach a fake handle with a store that returns no
        # current generation. We need to wire it after the
        # session is constructed so the attribute lookup
        # succeeds.
        class _FakeStore:
            def get_setting(self, _key: str) -> str | None:
                return None

        class _FakeHandle:
            store = _FakeStore()
            is_stale = False

        session.explore_index = _FakeHandle()
        workspace = MockWorkspaceRoot(tmp_path)
        completed = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=b"M  a.py\n",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ):
            result = handle_git_status(
                session, workspace, {"format": "compact"}
            )
        payload = json.loads(result.content[0].text)
        assert payload["index_used"] is False
        assert payload["index_status"] == "stale"
        assert payload["fallback_reason"] == "no_committed_generation"

    def test_status_compact_attaches_changed_symbols_when_index_current(
        self, tmp_path: Path
    ) -> None:
        """AC-06: when the index is current, compact ``git_status``
        attaches bounded changed-symbol hints per changed path
        so the agent does not have to repeat a second lookup.
        """
        from ralph.mcp.explore.handlers import build_explore_index
        from ralph.mcp.explore.pipeline import ReindexOptions, reindex

        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()
        (workspace_dir / "a.py").write_text(
            "def hello():\n    return 1\n"
        )
        handle = build_explore_index(workspace_dir)
        reindex(handle.store, workspace_dir, options=ReindexOptions(timeout_ms=5000))
        try:
            session = MockSession({GIT_STATUS_READ_CAPABILITY})
            session.explore_index = handle
            workspace = MockWorkspaceRoot(workspace_dir)
            completed = subprocess.CompletedProcess(
                args=["git", "status", "--porcelain"],
                returncode=0,
                stdout=b"M  a.py\n",
                stderr=b"",
            )
            with patch(
                "ralph.mcp.tools.git_read.run_git_command_lenient",
                return_value=completed,
            ):
                result = handle_git_status(
                    session, workspace, {"format": "compact"}
                )
            payload = json.loads(result.content[0].text)
            assert payload["index_used"] is True
            assert payload["index_status"] == "current"
            assert payload["index_generation"] >= 1
            assert payload["fallback_reason"] is None
            # The a.py path must carry at least one symbol hint.
            assert "a.py" in payload["changed_symbols"]
            symbols = payload["changed_symbols"]["a.py"]
            assert symbols, "expected at least one changed-symbol hint"
            first = symbols[0]
            assert first["qualified_name"]
            assert first["kind"]
            assert first["symbol_id"]
            assert first["span_id"]
        finally:
            handle.store.close()
