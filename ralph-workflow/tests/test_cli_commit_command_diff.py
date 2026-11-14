from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import commit as commit_module

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize("head_valid", [True, False])
def test_working_tree_diff_strips_lone_surrogates_from_git_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    head_valid: bool,
) -> None:
    (tmp_path / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        """#!/bin/sh
if [ "$1" = "rev-parse" ]; then
  [ "$FAKE_HEAD_VALID" = "1" ]
  exit $?
fi
if [ "$1" = "diff" ]; then
  printf 'diff\\n+\\244\\n'
  exit 0
fi
if [ "$1" = "ls-files" ]; then
  exit 0
fi
exit 2
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("FAKE_HEAD_VALID", "1" if head_valid else "0")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    diff = commit_module.working_tree_diff(tmp_path)

    assert "diff" in diff
    assert "\udca4" not in diff
    diff.encode("utf-8")
