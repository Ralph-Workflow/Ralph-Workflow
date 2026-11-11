from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.tools.coordination import CapabilityDeniedError, InvalidParamsError
from ralph.mcp.tools.git_read import (
    GIT_DIFF_READ_CAPABILITY,
    handle_git_diff,
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
        lambda **_kwargs: (tmp_path, False, None),
    )


class TestHandleGitDiff:
    def test_diff_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_diff(session, workspace, {"args": []})

    def test_diff_accepts_args(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        completed = subprocess.CompletedProcess(
            args=["git", "diff", "--staged"],
            returncode=0,
            stdout=b"diff --staged content",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ) as mock_git:
            result = handle_git_diff(session, workspace, {"args": ["--staged"]})
            assert result.is_error is False
            assert mock_git.called

    def test_diff_summary_rejects_negative_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": -1},
            )

    def test_diff_summary_rejects_zero_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 0},
            )

    def test_diff_summary_rejects_bool_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": True},
            )

    def test_diff_summary_rejects_malformed_string_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": "not-an-int"},
            )

    def test_diff_summary_rejects_non_integer_float_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 1.5},
            )

    def test_diff_summary_rejects_oversized_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 1_000_000},
            )

    def test_diff_summary_accepts_positive_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        diff_text = b"1234567890"
        numstat = subprocess.CompletedProcess(
            args=["git", "diff", "--numstat"],
            returncode=0,
            stdout=b"1\t0\ta.py\n",
            stderr=b"",
        )

        class _FakeStdout:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self._offset = 0

            def read(self, n: int) -> bytes:
                if self._offset >= len(self._data):
                    return b""
                chunk = self._data[self._offset : self._offset + n]
                self._offset += len(chunk)
                return chunk

        class _FakeProcess:
            def __init__(self, data: bytes) -> None:
                self.stdout = _FakeStdout(data)
                self.stderr = None

            def communicate_and_cleanup(self, timeout: float) -> tuple[bytes, bytes]:
                return (b"", b"")

        class _FakeManager:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def spawn(self, *args: object, **kwargs: object) -> _FakeProcess:
                return _FakeProcess(self._data)

        with (
            patch(
                "ralph.mcp.tools.git_read.run_git_command_lenient",
                return_value=numstat,
            ),
            patch(
                "ralph.mcp.tools.git_read.get_process_manager",
                return_value=_FakeManager(diff_text),
            ),
        ):
            result = handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 5},
            )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["format"] == "summary"
        assert payload["max_bytes"] == 5
        assert payload["truncated"] is True
        assert payload["diff_excerpt"] == diff_text[:5].decode("utf-8")

    def test_diff_rejects_output_equal_flag(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch("ralph.mcp.tools.git_read.run_git_command_lenient") as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--output=/tmp/leak.patch"]},
                )
            assert "--output" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_output_space_flag(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch("ralph.mcp.tools.git_read.run_git_command_lenient") as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--output", "/tmp/leak.patch"]},
                )
            assert "--output" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_short_output_flag(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch("ralph.mcp.tools.git_read.run_git_command_lenient") as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["-o", "/tmp/leak.patch"]},
                )
            assert "-o" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_ext_diff_flag(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch("ralph.mcp.tools.git_read.run_git_command_lenient") as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--ext-diff"]},
                )
            assert "--ext-diff" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_textconv_flag(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch("ralph.mcp.tools.git_read.run_git_command_lenient") as mock_git:
            with pytest.raises(InvalidParamsError):
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--textconv"]},
                )
            mock_git.assert_not_called()

    def test_diff_accepts_read_only_args(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        completed = subprocess.CompletedProcess(
            args=["git", "diff", "--stat"],
            returncode=0,
            stdout=b" a.py | 1 +\n 1 file changed, 1 insertion(+)\n",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ) as mock_git:
            result = handle_git_diff(
                session,
                workspace,
                {"args": ["--stat"]},
            )
        assert result.is_error is False
        assert mock_git.called

    def test_diff_summary_large_output_never_exceeds_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        large_text = b"a" * 200_000
        numstat = subprocess.CompletedProcess(
            args=["git", "diff", "--numstat"],
            returncode=0,
            stdout=b"1\t0\ta.py\n",
            stderr=b"",
        )

        class _FakeStdout:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self._offset = 0

            def read(self, n: int) -> bytes:
                if self._offset >= len(self._data):
                    return b""
                chunk = self._data[self._offset : self._offset + n]
                self._offset += len(chunk)
                return chunk

        class _FakeProcess:
            def __init__(self, data: bytes) -> None:
                self.stdout = _FakeStdout(data)
                self.stderr = None
                self.killed = False

            def communicate_and_cleanup(self, timeout: float) -> tuple[bytes, bytes]:
                return (b"", b"")

        class _FakeManager:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def spawn(self, *args: object, **kwargs: object) -> _FakeProcess:
                return _FakeProcess(self._data)

        cap = 1_000
        with (
            patch(
                "ralph.mcp.tools.git_read.run_git_command_lenient",
                return_value=numstat,
            ),
            patch(
                "ralph.mcp.tools.git_read.get_process_manager",
                return_value=_FakeManager(large_text),
            ),
        ):
            result = handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": cap},
            )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["truncated"] is True
        assert len(payload["diff_excerpt"].encode("utf-8")) == cap
        assert payload["diff_excerpt"] == "a" * cap

    def test_diff_summary_unicode_output_never_exceeds_max_bytes(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        unicode_char = "\u4e2d"
        unicode_text = (unicode_char * 30_000).encode("utf-8")
        assert len(unicode_text) > 50_000
        numstat = subprocess.CompletedProcess(
            args=["git", "diff", "--numstat"],
            returncode=0,
            stdout=b"1\t0\ta.py\n",
            stderr=b"",
        )

        class _FakeStdout:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self._offset = 0

            def read(self, n: int) -> bytes:
                if self._offset >= len(self._data):
                    return b""
                chunk = self._data[self._offset : self._offset + n]
                self._offset += len(chunk)
                return chunk

        class _FakeProcess:
            def __init__(self, data: bytes) -> None:
                self.stdout = _FakeStdout(data)
                self.stderr = None

            def communicate_and_cleanup(self, timeout: float) -> tuple[bytes, bytes]:
                return (b"", b"")

        class _FakeManager:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def spawn(self, *args: object, **kwargs: object) -> _FakeProcess:
                return _FakeProcess(self._data)

        cap = 50_000 - 1
        with (
            patch(
                "ralph.mcp.tools.git_read.run_git_command_lenient",
                return_value=numstat,
            ),
            patch(
                "ralph.mcp.tools.git_read.get_process_manager",
                return_value=_FakeManager(unicode_text),
            ),
        ):
            result = handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": cap},
            )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["truncated"] is True
        encoded_len = len(payload["diff_excerpt"].encode("utf-8"))
        assert encoded_len <= cap, f"encoded length {encoded_len} exceeds cap {cap}"
        assert "\ufffd" not in payload["diff_excerpt"], (
            "decoded excerpt contains U+FFFD replacement "
            "character; truncation walked past an incomplete "
            "UTF-8 sequence"
        )
