from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ralph.mcp.tools.exec import run_command


def test_an_exec_command_runs_in_a_private_temp_dir_that_is_removed_afterwards(
    tmp_path: Path,
) -> None:
    result = run_command(
        "sh", ["-c", 'touch "$TMPDIR/left-behind"; printf %s "$TMPDIR"'], tmp_path, 5000
    )
    used = Path(result.stdout.decode().strip())
    assert used.name.startswith("ra")
    assert not used.exists(), "the exec command's temp dir must be removed when it finishes"


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD grep setting is macOS-only")
def test_exec_commands_get_the_safe_grep_setting(tmp_path: Path) -> None:
    result = run_command("sh", ["-c", 'printf %s "$GREP_OPTIONS"'], tmp_path, 5000)
    assert "--exclude-dir=target" in result.stdout.decode()
