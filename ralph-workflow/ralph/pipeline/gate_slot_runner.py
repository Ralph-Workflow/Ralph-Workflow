from __future__ import annotations

import shlex
import sys
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.pipeline.publication_gate_slots import acquire_gate_slot
from ralph.process.manager._supervisor import run as run_supervised
from ralph.process.manager._supervisor import sanitize_process_environment

if TYPE_CHECKING:
    from collections.abc import Sequence

RUNNER_MODULE: Final[str] = "ralph.pipeline.gate_slot_runner"
_SHELL_SEPARATORS: Final[tuple[str, ...]] = ("&&", "||", ";", "|", "&")
_SHELL_ARGV_LENGTH: Final[int] = 3
_USAGE_EXIT_CODE: Final[int] = 2


def _command_segments(command: Sequence[str]) -> list[list[str]]:
    if len(command) >= _SHELL_ARGV_LENGTH and command[0] in ("sh", "bash") and command[1] == "-c":
        try:
            tokens = shlex.split(command[2])
        except ValueError:
            return [list(command)]
        segments: list[list[str]] = [[]]
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                segments.append([])
            else:
                segments[-1].append(token)
        return [segment for segment in segments if segment]
    return [list(command)]


def runs_declared_gate(command: Sequence[str], declared_gate: str | None) -> bool:
    if not declared_gate:
        return False
    try:
        gate = shlex.split(declared_gate)
    except ValueError:
        return False
    if not gate:
        return False
    return any(segment[: len(gate)] == gate for segment in _command_segments(command))


def gate_slot_command(command: Sequence[str], declared_gate: str | None) -> list[str]:
    if not runs_declared_gate(command, declared_gate):
        return list(command)
    return [sys.executable, "-m", RUNNER_MODULE, "--", *command]


def main(argv: Sequence[str]) -> int:
    sanitize_process_environment()
    command = list(argv[1:] if argv and argv[0] == "--" else argv)
    if not command:
        return _USAGE_EXIT_CODE
    handle = acquire_gate_slot()
    if handle is None:
        logger.warning("no host-wide gate slot became free; running the gate without one")
        return run_supervised(command)
    try:
        return run_supervised(command)
    finally:
        handle.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
