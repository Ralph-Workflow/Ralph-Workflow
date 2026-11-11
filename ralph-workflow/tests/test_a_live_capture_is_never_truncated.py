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
    parsed: object = json.loads(line)
    assert isinstance(parsed, dict)
    tag: object = parsed.get("w")
    assert isinstance(tag, str)
    return tag


def _write_one(root: Path, tag: str) -> None:
    log = get_or_create_raw_overflow_log(root, raw_log_unit_id_for(_CONFIG), model=_CONFIG.model)
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
    script = tmp_path / "writer.py"
    script.write_text(_WRITER_SCRIPT, encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
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
    reset_raw_overflow_path_state()
    _write_one(tmp_path, "phase-one")
    _write_one(tmp_path, "phase-two")

    lines = _capture_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert [_tag_of(line) for line in lines] == ["phase-one", "phase-two"]
