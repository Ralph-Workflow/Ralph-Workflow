"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

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

# =============================================================================
# Mock infrastructure
# =============================================================================


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

    # --- AC-06: max_bytes is strictly bounded -------------------------------

    def test_diff_summary_rejects_negative_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """AC-06: ``max_bytes`` must be a positive integer in
        ``[1, 50000]``. The previous lenient coercion silently
        truncated a 10-byte diff to ``[:-1]`` for ``max_bytes=-1``
        and surfaced a misleading ``truncated=true`` payload.
        The strict contract fails closed.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": -1},
            )

    def test_diff_summary_rejects_zero_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 0},
            )

    def test_diff_summary_rejects_bool_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": True},
            )

    def test_diff_summary_rejects_malformed_string_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": "not-an-int"},
            )

    def test_diff_summary_rejects_non_integer_float_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 1.5},
            )

    def test_diff_summary_rejects_oversized_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 1_000_000},
            )

    def test_diff_summary_accepts_positive_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """AC-06: a positive ``max_bytes`` returns a real
        summary payload with a capped excerpt.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        diff_text = b"1234567890"
        numstat = subprocess.CompletedProcess(
            args=["git", "diff", "--numstat"],
            returncode=0,
            stdout=b"1\t0\ta.py\n",
            stderr=b"",
        )
        # AC-06: the streaming path uses ``get_process_manager``
        # to spawn the diff subprocess. The mock provides a fake
        # ``_FakeProcess`` whose ``stdout.read`` returns chunks
        # of the diff text. The streaming cap stops the read
        # loop once ``max_bytes`` bytes have been collected.

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
        # The excerpt must equal ``diff_text[:5]`` (5 bytes),
        # proving the bounded contract is honored.
        assert payload["diff_excerpt"] == diff_text[:5].decode("utf-8")

    # --- AC-06: read-only contract for args --------------------------

    def test_diff_rejects_output_equal_flag(
        self, tmp_path: Path
    ) -> None:
        """AC-06: ``--output=...`` is an output-writing flag
        that would let git write to the workspace. The
        read-only ``GitDiffRead`` contract rejects it at parse
        time, before git is invoked.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        # Spy on the runner; the test fails if it is called.
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient"
        ) as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--output=/tmp/leak.patch"]},
                )
            assert "--output" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_output_space_flag(
        self, tmp_path: Path
    ) -> None:
        """AC-06: ``--output PATH`` (space-separated) is also
        an output-writing flag. The parser MUST reject it.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient"
        ) as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--output", "/tmp/leak.patch"]},
                )
            assert "--output" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_short_output_flag(
        self, tmp_path: Path
    ) -> None:
        """AC-06: ``-o`` is the short form of ``--output``.
        The parser MUST reject the short form to close the
        short-flag path.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient"
        ) as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["-o", "/tmp/leak.patch"]},
                )
            assert "-o" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_ext_diff_flag(
        self, tmp_path: Path
    ) -> None:
        """AC-06: ``--ext-diff`` invokes an external helper
        (``GIT_EXTERNAL_DIFF``), bypassing the read-only
        intent of the MCP tool. The parser MUST reject it.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient"
        ) as mock_git:
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--ext-diff"]},
                )
            assert "--ext-diff" in str(exc_info.value)
            mock_git.assert_not_called()

    def test_diff_rejects_textconv_flag(
        self, tmp_path: Path
    ) -> None:
        """AC-06: ``--textconv`` invokes an external helper
        (``GIT_TEXTConv``), bypassing the read-only intent.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient"
        ) as mock_git:
            with pytest.raises(InvalidParamsError):
                handle_git_diff(
                    session,
                    workspace,
                    {"args": ["--textconv"]},
                )
            mock_git.assert_not_called()

    def test_diff_accepts_read_only_args(
        self, tmp_path: Path
    ) -> None:
        """AC-06: read-only flags (``--stat``, ``--name-only``,
        ``--numstat``, ``--shortstat``, ``--staged``,
        ``--unified=N``, ``--diff-filter=...``) are forwarded
        unchanged. The parser MUST accept them without
        rejecting.
        """
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

    # --- AC-06: byte-bounded streaming excerpt ------------------------

    def test_diff_summary_large_output_never_exceeds_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """AC-06: when the diff is much larger than ``max_bytes``,
        the returned excerpt MUST be exactly ``max_bytes`` bytes
        long (UTF-8 encoded). The streaming cap stops reading as
        soon as the cap is reached, so the caller never holds an
        unbounded buffer; the underlying subprocess is terminated
        early when the cap is hit.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        # 200 KiB of ASCII content. The cap is 1 KiB.
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
        # The encoded excerpt length MUST equal ``cap`` exactly.
        # The streaming cap enforces this at the byte boundary.
        assert len(payload["diff_excerpt"].encode("utf-8")) == cap
        # The full content was 200_000 bytes, but the cap stopped
        # the read loop after ``cap`` bytes were collected.
        assert payload["diff_excerpt"] == "a" * cap

    def test_diff_summary_unicode_output_never_exceeds_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """AC-06: when the diff contains multi-byte UTF-8 content,
        the returned excerpt MUST be at most ``max_bytes`` bytes
        long. Any partial UTF-8 sequence at the truncation
        boundary is replaced with ``U+FFFD`` on decode so the
        returned string never contains invalid UTF-8.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        # 100 KiB of 3-byte UTF-8 (CJK characters). The cap is
        # deliberately set to a non-character-aligned value so
        # the truncation boundary is guaranteed to fall inside a
        # UTF-8 sequence.
        unicode_char = "\u4e2d"  # 中, 3 bytes in UTF-8
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

        # Pick a cap that is not a multiple of 3 so the
        # truncation boundary falls inside a 3-byte UTF-8
        # sequence. The cap must be within the strict
        # ``max_bytes`` validation range (``[1, 50000]``).
        cap = 50_000 - 1  # 49999, NOT a multiple of 3
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
        # AC-06: the encoded excerpt length MUST be at most
        # ``cap`` bytes. The streaming cap stops at exactly
        # ``cap`` raw bytes and the UTF-8 boundary walk
        # trims any partial character at the end, so the
        # decoded string's encoded length is ``<= cap``.
        encoded_len = len(payload["diff_excerpt"].encode("utf-8"))
        assert encoded_len <= cap, (
            f"encoded length {encoded_len} exceeds cap {cap}"
        )
        # The decoded string is valid UTF-8 (no exception on
        # re-encode) and contains complete characters only;
        # no ``U+FFFD`` replacement character should appear
        # because the boundary walk guarantees clean
        # truncation.
        assert "\ufffd" not in payload["diff_excerpt"], (
            "decoded excerpt contains U+FFFD replacement "
            "character; truncation walked past an incomplete "
            "UTF-8 sequence"
        )
