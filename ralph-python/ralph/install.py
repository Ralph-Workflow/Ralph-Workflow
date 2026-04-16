"""Installation helpers for refreshing Ralph from the current checkout."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence


class RunCommand(Protocol):
    def __call__(self, command: Sequence[str], *, cwd: Path) -> None: ...


def _run_command(command: Sequence[str], *, cwd: Path) -> None:
    subprocess.run(list(command), cwd=cwd, check=True)


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
