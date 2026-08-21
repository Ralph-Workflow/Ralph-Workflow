"""A capture another run is still writing must not be truncated.

``_PATH_STATE`` is process-local, so the FIRST writer in each process
truncated the file it opened. Same-workspace parallel workers are
separate OS processes sharing one workspace root, and workers of one
phase resolve the same agent -- so the same unit id and the same path.
One worker's entire transcript was replaced by another's, and because
the survivor's bytes are well-formed JSONL the corruption detector
reported nothing: the silent-loss shape the capture exists to make
impossible.

Truncation is right for a STALE file and wrong for a LIVE one, and
modification time is what separates them. These tests drive the real
writer through its public factory rather than the private predicate,
because the defect was never in the predicate -- it was in which
question the constructor asked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ralph.config.agent_config import AgentConfig
from ralph.display.raw_overflow import (
    get_or_create_raw_overflow_log,
    raw_log_path_for,
    raw_log_unit_id_for,
    reset_raw_overflow_path_state,
)

_CONFIG = AgentConfig(cmd="claude", model="opus")


def _capture_path(root: Path) -> Path:
    return raw_log_path_for(root, raw_log_unit_id_for(_CONFIG), model=_CONFIG.model)


def _tag_of(line: str) -> str:
    """Return the writer tag a captured frame carries."""
    parsed: object = json.loads(line)
    assert isinstance(parsed, dict)
    tag: object = parsed.get("w")
    assert isinstance(tag, str)
    return tag


def _write_one(root: Path, tag: str) -> None:
    log = get_or_create_raw_overflow_log(
        root, raw_log_unit_id_for(_CONFIG), model=_CONFIG.model
    )
    frame: dict[str, object] = {"w": tag}
    log.append(json.dumps(frame) + "\n")
    log.close()


_WRITER_SCRIPT = r"""
import json, sys
from pathlib import Path
from ralph.config.agent_config import AgentConfig
from ralph.display.raw_overflow import (
    get_or_create_raw_overflow_log,
    raw_log_unit_id_for,
)

root, tag, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
config = AgentConfig(cmd="claude", model="opus")
log = get_or_create_raw_overflow_log(
    root, raw_log_unit_id_for(config), model=config.model
)
for index in range(count):
    log.append(json.dumps({"w": tag, "i": index}) + "\n")
log.close()
"""


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_two_processes_sharing_a_workspace_keep_both_transcripts(
    tmp_path: Path,
) -> None:
    """The defect itself, which only a second OS process can show.

    Same-workspace parallel workers are separate processes, and workers
    of one phase resolve the same agent -- the same unit id, the same
    path. This cannot be simulated in-process: the writer deliberately
    remembers which paths are ITS OWN and truncates those however many
    runs ago it wrote them, so a fake "sibling" made by clearing the
    path state is correctly not believed.
    """
    script = tmp_path / "writer.py"
    script.write_text(_WRITER_SCRIPT, encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    # PYTHONPATH pinned to the tree under test: this venv resolves
    # ``ralph`` to a sibling worktree otherwise, and the subprocess
    # would exercise somebody else's writer.
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(repo_root),
    }

    first = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path), "first", "60"],
        cwd=repo_root,
        env=env,
    )
    try:
        second = subprocess.run(
            [sys.executable, str(script), str(tmp_path), "second", "60"],
            cwd=repo_root,
            env=env,
            check=False,
            capture_output=True,
        )
    finally:
        first.wait(timeout=20)
    assert second.returncode == 0, second.stderr.decode()

    written = _capture_path(tmp_path).read_text(encoding="utf-8").splitlines()
    tags = [_tag_of(line) for line in written if line.strip()]
    assert tags.count("first") == 60, tags[:5]
    assert tags.count("second") == 60, tags[:5]
    assert b"\x00" not in _capture_path(tmp_path).read_bytes()


def test_a_stale_capture_from_an_earlier_run_is_still_truncated(
    tmp_path: Path,
) -> None:
    """The guard must not turn every capture into an append-forever log.

    A file left by a run that finished before this process started is
    exactly what truncation is for; keeping it would grow without bound
    and mix a dead run's frames into this one's transcript.
    """
    reset_raw_overflow_path_state()
    _write_one(tmp_path, "yesterdays-run")
    path = _capture_path(tmp_path)
    stale = time.time() - 86400
    os.utime(path, (stale, stale))

    reset_raw_overflow_path_state()
    _write_one(tmp_path, "todays-run")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [_tag_of(line) for line in lines] == ["todays-run"]


def test_a_second_writer_in_this_process_still_appends(tmp_path: Path) -> None:
    """The in-process case is unchanged: one file per agent per run."""
    reset_raw_overflow_path_state()
    _write_one(tmp_path, "phase-one")
    _write_one(tmp_path, "phase-two")

    lines = _capture_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert [_tag_of(line) for line in lines] == ["phase-one", "phase-two"]
