from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ralph.mcp.tools import exec_overlay

if TYPE_CHECKING:
    import pytest


def test_process_identity_matches_matching_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = os.getpid()
    monkeypatch.setattr(exec_overlay, "_current_process_identity", lambda: (pid, 123.0))

    assert exec_overlay._process_identity_matches(pid, 123.0) is True


def test_process_identity_matches_rejects_reused_pid_with_different_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = os.getpid()
    monkeypatch.setattr(exec_overlay, "_current_process_identity", lambda: (pid, 456.0))

    assert exec_overlay._process_identity_matches(pid, 123.0) is False
