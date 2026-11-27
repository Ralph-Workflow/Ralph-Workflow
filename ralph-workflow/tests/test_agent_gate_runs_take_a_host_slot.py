from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.gate_slot_runner import gate_slot_command
from ralph.pipeline.publication_gate_slots import GATE_SLOT_DIR_ENV, GATE_SLOTS_ENV

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]

_PROBE = (
    "import fcntl, sys\n"
    "handle = open(sys.argv[1], 'a')\n"
    "try:\n"
    "    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except OSError:\n"
    "    print('slot held')\n"
    "else:\n"
    "    print('slot free')\n"
)


@pytest.mark.parametrize(
    "command",
    [
        ["sh", "-c", "bin/rails verify"],
        ["sh", "-c", "cd app && bin/rails verify --fail-fast"],
        ["bin/rails", "verify"],
    ],
)
def test_a_command_that_runs_the_declared_gate_is_wrapped(command: list[str]) -> None:
    wrapped = gate_slot_command(command, "bin/rails verify")

    assert wrapped[:4] == [sys.executable, "-m", "ralph.pipeline.gate_slot_runner", "--"]
    assert wrapped[4:] == command


@pytest.mark.parametrize(
    "command",
    [["sh", "-c", "bin/rails test test/models/user_test.rb"], ["make", "lint"]],
)
def test_a_narrower_command_is_left_alone(command: list[str]) -> None:
    assert gate_slot_command(command, "bin/rails verify") == command
    assert gate_slot_command(command, None) == command


def test_the_gate_runs_while_holding_a_host_slot(tmp_path: Path) -> None:
    env = {**os.environ, GATE_SLOT_DIR_ENV: str(tmp_path), GATE_SLOTS_ENV: "1"}
    runner = [sys.executable, "-m", "ralph.pipeline.gate_slot_runner", "--"]

    result = subprocess.run(
        [*runner, sys.executable, "-c", _PROBE, str(tmp_path / "slot-0.lock")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "slot held"


def test_the_gate_exit_code_is_passed_through(tmp_path: Path) -> None:
    env = {**os.environ, GATE_SLOT_DIR_ENV: str(tmp_path), GATE_SLOTS_ENV: "1"}
    runner = [sys.executable, "-m", "ralph.pipeline.gate_slot_runner", "--"]

    result = subprocess.run([*runner, "sh", "-c", "exit 5"], env=env, check=False)

    assert result.returncode == 5
