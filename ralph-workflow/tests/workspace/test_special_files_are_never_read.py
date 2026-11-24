from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.workspace import WORKSPACE_READ_CAPABILITY, handle_grep_files
from ralph.workspace import fs as fs_module
from ralph.workspace.fs import FsWorkspace
from tests.mock_session import MockSession

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.timeout_seconds(5)


def _workspace_with_fifo(tmp_path: Path) -> FsWorkspace:
    (tmp_path / "notes.md").write_text("<<<<<<< ours\n")
    (tmp_path / "rootfs" / "dev").mkdir(parents=True)
    os.mkfifo(tmp_path / "rootfs" / "dev" / "random")
    return FsWorkspace(tmp_path)


def test_stat_reports_a_fifo_as_special(tmp_path: Path) -> None:
    workspace = _workspace_with_fifo(tmp_path)

    assert workspace.stat("rootfs/dev/random")["type"] == "special"


def test_read_refuses_a_fifo_instead_of_blocking(tmp_path: Path) -> None:
    workspace = _workspace_with_fifo(tmp_path)

    with pytest.raises(OSError, match="not a regular file"):
        workspace.read("rootfs/dev/random")


def test_grep_over_the_workspace_skips_special_files(tmp_path: Path) -> None:
    workspace = _workspace_with_fifo(tmp_path)

    result = handle_grep_files(
        MockSession(WORKSPACE_READ_CAPABILITY),
        workspace,
        {"pattern": "^<<<<<<<", "path": ".", "use_index": "never"},
    )

    payload = json.loads(result.content[0].text)
    assert [m["path"] for m in payload["matches"]] == ["notes.md"]


@pytest.mark.parametrize(
    "read_special",
    [
        pytest.param(lambda ws: ws.read("rootfs/dev/random"), id="read"),
        pytest.param(lambda ws: ws.snapshot("rootfs/dev/random"), id="snapshot"),
        pytest.param(lambda ws: ws.read_lines("rootfs/dev/random"), id="read_lines"),
        pytest.param(lambda ws: ws.read_lines("rootfs/dev/random", tail=5), id="read_lines_tail"),
        pytest.param(lambda ws: ws.read_bytes("rootfs/dev/random"), id="read_bytes"),
    ],
)
def test_every_read_path_refuses_a_fifo(
    tmp_path: Path, read_special: Callable[[FsWorkspace], object]
) -> None:
    workspace = _workspace_with_fifo(tmp_path)

    with pytest.raises(OSError, match="not a regular file"):
        read_special(workspace)


def test_fs_workspace_opens_content_only_through_the_regular_file_guard() -> None:
    source = Path(fs_module.__file__).read_text(encoding="utf-8")
    raw_read_opens = re.findall(r"(?<!os)\.open\((?![^)]*[\"']a[\"'])[^)]*\)", source)

    assert raw_read_opens == []
