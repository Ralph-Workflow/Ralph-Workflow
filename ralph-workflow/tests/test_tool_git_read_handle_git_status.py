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


@pytest.fixture(autouse=True)
def _resolve_cwd_without_git_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "ralph.mcp.tools.git_read.resolve_git_cwd",
        lambda *, workspace_root, requested_cwd: (tmp_path, False, None),
    )


class TestHandleGitStatus:
    def test_status_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
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

    def test_status_compact_emits_index_metadata_without_handle(self, tmp_path: Path) -> None:
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
            result = handle_git_status(session, workspace, {"format": "compact"})
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["format"] == "compact"
        assert "index_used" in payload
        assert "index_status" in payload
        assert "fallback_reason" in payload
        assert payload["index_used"] is False
        assert payload["index_status"] == "unavailable"
        assert payload["index_generation"] == 0
        assert payload["changed_symbols"] == {}
        assert payload["fallback_reason"] == "index_not_attached"

    def test_status_compact_marks_index_stale_when_generation_zero(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})

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
            result = handle_git_status(session, workspace, {"format": "compact"})
        payload = json.loads(result.content[0].text)
        assert payload["index_used"] is False
        assert payload["index_status"] == "stale"
        assert payload["fallback_reason"] == "no_committed_generation"

    def test_status_compact_marks_index_stale_when_dirty_paths_pending(
        self, tmp_path: Path
    ) -> None:
        from ralph.mcp.explore.handlers import build_explore_index
        from ralph.mcp.explore.pipeline import ReindexOptions, reindex

        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()
        (workspace_dir / "a.py").write_text("def hello():\n    return 1\n")
        handle = build_explore_index(workspace_dir)
        reindex(handle.store, workspace_dir, options=ReindexOptions(timeout_ms=5000))
        try:
            handle.store.mark_dirty("a.py", reason="mutated", source_tool="write_file")
            session = MockSession({GIT_STATUS_READ_CAPABILITY})
            session.explore_index = handle
            workspace = MockWorkspaceRoot(workspace_dir)
            completed = subprocess.CompletedProcess(
                args=["git", "status", "--porcelain"],
                returncode=0,
                stdout=b"M  a.py\n",
                stderr=b"",
            )
            with (
                patch(
                    "ralph.mcp.tools.git_read.resolve_git_cwd",
                    return_value=(workspace_dir, False, workspace_dir),
                ),
                patch(
                    "ralph.mcp.tools.git_read.run_git_command_lenient",
                    return_value=completed,
                ),
            ):
                result = handle_git_status(session, workspace, {"format": "compact"})
            payload = json.loads(result.content[0].text)
            assert payload["index_used"] is False
            assert payload["index_status"] == "stale"
            assert payload["fallback_reason"] == "index_reports_stale"
            assert payload["changed_symbols"] == {}
        finally:
            handle.store.close()

    def test_status_compact_attaches_changed_symbols_when_index_current(
        self, tmp_path: Path
    ) -> None:
        from ralph.mcp.explore.handlers import build_explore_index
        from ralph.mcp.explore.pipeline import ReindexOptions, reindex

        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()
        (workspace_dir / "a.py").write_text("def hello():\n    return 1\n")
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
                result = handle_git_status(session, workspace, {"format": "compact"})
            payload = json.loads(result.content[0].text)
            assert payload["index_used"] is True
            assert payload["index_status"] == "current"
            assert payload["index_generation"] >= 1
            assert payload["fallback_reason"] is None
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

    def test_status_compact_marks_index_unavailable_when_dirty_paths_raises(
        self, tmp_path: Path
    ) -> None:

        class _FailingDirtyPathsStore:
            def get_setting(self, _key: str) -> str:
                return "1"

            def peek_dirty_paths(self) -> list[str]:
                raise RuntimeError("boom")

        class _FakeHandle:
            store = _FailingDirtyPathsStore()
            reindex_in_progress = False

        session = MockSession({GIT_STATUS_READ_CAPABILITY})
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
            result = handle_git_status(session, workspace, {"format": "compact"})
        payload = json.loads(result.content[0].text)
        assert payload["index_used"] is False
        assert payload["index_status"] == "unavailable"
        assert payload["fallback_reason"] == "dirty_paths_read_failed"
        assert payload["changed_symbols"] == {}

    def test_status_compact_marks_index_stale_when_deleted_file_present(
        self, tmp_path: Path
    ) -> None:
        from ralph.mcp.explore.handlers import build_explore_index
        from ralph.mcp.explore.pipeline import ReindexOptions, reindex
        from ralph.mcp.explore.store import FileRow

        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()
        (workspace_dir / "a.py").write_text("def hello():\n    return 1\n")
        handle = build_explore_index(workspace_dir)
        reindex(handle.store, workspace_dir, options=ReindexOptions(timeout_ms=5000))
        try:
            handle.store.upsert_file(
                FileRow(
                    path="gone.py",
                    content_hash="deadbeef" * 8,
                    size_bytes=0,
                    mtime_ns=0,
                    language="python",
                    indexed_generation=1,
                    indexed_at=0.0,
                    is_deleted=True,
                )
            )
            session = MockSession({GIT_STATUS_READ_CAPABILITY})
            session.explore_index = handle
            workspace = MockWorkspaceRoot(workspace_dir)
            completed = subprocess.CompletedProcess(
                args=["git", "status", "--porcelain"],
                returncode=0,
                stdout=b"M  a.py\n",
                stderr=b"",
            )
            with (
                patch(
                    "ralph.mcp.tools.git_read.resolve_git_cwd",
                    return_value=(workspace_dir, False, workspace_dir),
                ),
                patch(
                    "ralph.mcp.tools.git_read.run_git_command_lenient",
                    return_value=completed,
                ),
            ):
                result = handle_git_status(session, workspace, {"format": "compact"})
            payload = json.loads(result.content[0].text)
            assert payload["index_used"] is False
            assert payload["index_status"] == "stale"
            assert payload["fallback_reason"] == "index_reports_stale"
            assert payload["changed_symbols"] == {}
        finally:
            handle.store.close()

    def test_status_compact_does_not_materialize_files_table(self, tmp_path: Path) -> None:
        from ralph.mcp.explore.handlers import build_explore_index
        from ralph.mcp.explore.pipeline import ReindexOptions, reindex

        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()
        (workspace_dir / "a.py").write_text("def hello():\n    return 1\n")
        handle = build_explore_index(workspace_dir)
        reindex(handle.store, workspace_dir, options=ReindexOptions(timeout_ms=5000))
        iter_calls = {"n": 0}
        has_deleted_calls = {"n": 0}
        try:
            inner = handle.store

            class _SpyingStore(type(inner)):
                def __init__(self) -> None:
                    self._inner = inner

                def __getattr__(self, name: str) -> object:
                    return getattr(self._inner, name)

                def has_deleted_files(self) -> bool:
                    has_deleted_calls["n"] += 1
                    return self._inner.has_deleted_files()

                def iter_files(self) -> object:
                    iter_calls["n"] += 1
                    return self._inner.iter_files()

            spy: object = _SpyingStore()
            handle.__dict__["store"] = spy
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
                result = handle_git_status(session, workspace, {"format": "compact"})
            payload = json.loads(result.content[0].text)
            assert payload["index_status"] == "current"
            assert has_deleted_calls["n"] >= 1
            assert iter_calls["n"] == 0
        finally:
            handle.store.close()

    def test_status_compact_attaches_deterministic_score_per_card(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        completed = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=b"M  src/a.py\n M b.py\n?? c.txt\n",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ):
            result = handle_git_status(session, workspace, {"format": "compact"})
        payload = json.loads(result.content[0].text)
        cards = payload["paths"]
        for card in cards:
            assert isinstance(card["score"], int)
            assert isinstance(card["score_reasons"], list)
            assert card["score_reasons"], f"empty score_reasons for {card['path']!r}"
        ranking = payload["ranking"]
        assert ranking["scheme"] == "deterministic_integer_score"
        assert ranking["tiebreak"] == "lexicographic_path"
        assert any("+100 role=unstaged" in c for c in ranking["components"])
        assert any("+80 role=staged" in c for c in ranking["components"])
        assert any("+60 role=untracked" in c for c in ranking["components"])
        assert any("-50 generated_or_vendor_path" in c for c in ranking["components"])

    def test_status_compact_sorts_cards_by_score_descending(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        completed = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=b"?? b.txt\nM  a.py\n M c.py\n",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ):
            result = handle_git_status(session, workspace, {"format": "compact"})
        payload = json.loads(result.content[0].text)
        cards = payload["paths"]
        ordered_paths = [c["path"] for c in cards]
        assert ordered_paths == ["c.py", "a.py", "b.txt"]
        assert [c["score"] for c in cards] == [100, 80, 60]

    def test_status_compact_lexicographic_tiebreak(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        completed = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=b" M b.py\n M a.py\n",
            stderr=b"",
        )
        with (
            patch(
                "ralph.mcp.tools.git_read.resolve_git_cwd",
                return_value=(tmp_path, False, None),
            ),
            patch(
                "ralph.mcp.tools.git_read.run_git_command_lenient",
                return_value=completed,
            ),
        ):
            result = handle_git_status(session, workspace, {"format": "compact"})
        payload = json.loads(result.content[0].text)
        cards = payload["paths"]
        assert [c["path"] for c in cards] == ["a.py", "b.py"]
        assert cards[0]["score"] == cards[1]["score"] == 100

    def test_status_compact_generated_or_vendor_path_demoted(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        completed = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=b" M vendor/foo.py\n M a.py\n",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ):
            result = handle_git_status(session, workspace, {"format": "compact"})
        payload = json.loads(result.content[0].text)
        cards = payload["paths"]
        assert [c["path"] for c in cards] == ["a.py", "vendor/foo.py"]
        assert cards[0]["score"] == 100
        assert cards[1]["score"] == 50
        assert "-50 generated_or_vendor_path" in cards[1]["score_reasons"]
