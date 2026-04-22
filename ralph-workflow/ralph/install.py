"""Installation helpers for refreshing Ralph from the current checkout."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ralph.executor.process import ProcessExecutionError, run_process

if TYPE_CHECKING:
    from collections.abc import Sequence


class RunCommand(Protocol):
    def __call__(self, command: Sequence[str], *, cwd: Path) -> None: ...


def _run_command(command: Sequence[str], *, cwd: Path) -> None:
    cmd = tuple(command)
    result = run_process(cmd[0], cmd[1:], cwd=cwd)
    if not result.succeeded:
        raise ProcessExecutionError(
            cmd,
            f"Command failed with exit code {result.returncode}: {' '.join(cmd)}",
        )


def install_current_checkout(
    *,
    package_dir: Path,
    run: RunCommand = _run_command,
    python_executable: str,
    pipx_executable: str | None,
) -> None:
    """Install the current checkout and refresh pipx when available."""
    run((python_executable, "-m", "pip", "install", "-e", ".[dev]"), cwd=package_dir)

    if pipx_executable is None:
        return

    run(
        (
            pipx_executable,
            "install",
            "--force",
            "--editable",
            str(package_dir),
        ),
        cwd=package_dir,
    )


def main() -> int:
    """Refresh Ralph from the current checkout for local and pipx use."""
    install_current_checkout(
        package_dir=Path(__file__).resolve().parents[1],
        run=_run_command,
        python_executable=sys.executable,
        pipx_executable=shutil.which("pipx"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
