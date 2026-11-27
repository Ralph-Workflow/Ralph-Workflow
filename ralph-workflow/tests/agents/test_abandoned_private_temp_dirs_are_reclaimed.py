from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from ralph.agents.invoke.agent_temp_dir import (
    create_private_tmpdir,
    remove_private_tmpdir,
    sweep_abandoned_private_tmpdirs,
)
from ralph.executor.process import ProcessRunOptions, run_process


def _created_by_a_process_that_has_exited(root: Path) -> Path:
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from ralph.agents.invoke.agent_temp_dir import create_private_tmpdir\n"
        "tmpdir = create_private_tmpdir(Path(sys.argv[1]))\n"
        "(tmpdir / 'clr-debug-pipe-1-in').write_text('left behind')\n"
        "print(tmpdir)\n"
    )
    result = run_process(
        sys.executable,
        ("-c", code, str(root)),
        options=ProcessRunOptions(timeout=30.0, label="abandoned-tmpdir-owner"),
    )
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.strip())


def test_a_temp_dir_whose_owner_died_is_reclaimed(tmp_path: Path) -> None:
    abandoned = _created_by_a_process_that_has_exited(tmp_path)
    assert abandoned.exists()

    assert sweep_abandoned_private_tmpdirs(tmp_path) == 1

    assert not abandoned.exists()


def test_a_temp_dir_whose_owner_is_alive_is_kept(tmp_path: Path) -> None:
    live = create_private_tmpdir(tmp_path)
    try:
        assert sweep_abandoned_private_tmpdirs(tmp_path) == 0
        assert live.exists()
    finally:
        remove_private_tmpdir(live)


def test_an_old_unstamped_temp_dir_from_before_stamping_is_reclaimed(tmp_path: Path) -> None:
    legacy = tmp_path / "ralegacy01"
    legacy.mkdir()
    recent = tmp_path / "rarecent01"
    recent.mkdir()
    old = time.time() - 13 * 3600
    os.utime(legacy, (old, old))

    sweep_abandoned_private_tmpdirs(tmp_path)

    assert not legacy.exists()
    assert recent.exists()


def test_unrelated_entries_are_never_touched(tmp_path: Path) -> None:
    other = tmp_path / "raw-data-dir"
    other.mkdir()
    file_entry = tmp_path / "ra12345678"
    file_entry.write_text("not a dir")

    sweep_abandoned_private_tmpdirs(tmp_path, now=time.time() + 10 * 86400)

    assert other.exists()
    assert file_entry.exists()
