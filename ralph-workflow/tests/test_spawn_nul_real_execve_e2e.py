from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy, SpawnOptions

pytestmark = pytest.mark.subprocess_e2e

_NUL = "\x00"
_ECHO_ARGV = "import sys; sys.stdout.write(sys.argv[1])"
_PROMPT = f'const masked = token.replace(/x/g, () => "{_NUL}".repeat(2));'
_CLEAN_PROMPT = 'const masked = token.replace(/x/g, () => "".repeat(2));'
_WAIT_TIMEOUT_SECONDS = 30.0


def test_real_spawn_delivers_a_nul_carrying_prompt_with_the_nul_stripped() -> None:
    pm = ProcessManager(policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False))

    with pm.spawn(
        [sys.executable, "-c", _ECHO_ARGV, _PROMPT],
        SpawnOptions(stdout=subprocess.PIPE, text=True, label="test:nul-argv"),
    ) as handle:
        stdout = handle.stdout
        assert stdout is not None
        received = stdout.read()
        handle.wait(timeout=_WAIT_TIMEOUT_SECONDS)

    assert received == _CLEAN_PROMPT


def test_real_spawn_still_accepts_a_pathlike_argument() -> None:
    pm = ProcessManager(policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False))

    with pm.spawn(
        [sys.executable, "-c", _ECHO_ARGV, Path(sys.executable)],
        SpawnOptions(stdout=subprocess.PIPE, text=True, label="test:pathlike-argv"),
    ) as handle:
        stdout = handle.stdout
        assert stdout is not None
        received = stdout.read()
        handle.wait(timeout=_WAIT_TIMEOUT_SECONDS)

    assert received == sys.executable
