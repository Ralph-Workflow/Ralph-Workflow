"""Conflict detection for the global ``ralph`` executable.

The update checker classifies package files, not console-script paths.  This
module resolves the executable to its owning package before asking it to classify
an existing install.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from os import environ as process_environ
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.executor.process import ProcessRunOptions, run_process
from ralph.update_check._install_kind import InstallKind
from ralph.update_check.environment import detect_install

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_PACKAGE_FILE_SCRIPT = "import pathlib, ralph; print(pathlib.Path(ralph.__file__))"


class ConflictResolution(StrEnum):
    """The user's selected action for an existing global install."""

    CONTINUE = "continue"
    REMOVE = "remove"
    ABORT = "abort"


@dataclass(frozen=True)
class ExistingInstall:
    """A global executable and the installation type that owns it."""

    executable: Path
    package_file: Path
    kind: InstallKind

    @property
    def remove_command(self) -> tuple[str, ...] | None:
        """Return the safe package-manager removal command, when one exists."""
        if self.kind is InstallKind.PIPX:
            return ("pipx", "uninstall", "ralph-workflow")
        if self.kind is InstallKind.UV_TOOL:
            return ("uv", "tool", "uninstall", "ralph-workflow")
        return None


def resolve_package_file(executable: str) -> Path | None:
    """Resolve a console script to its interpreter's installed package file."""
    script = Path(executable).resolve()
    try:
        first_line = script.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    if not first_line.startswith("#!"):
        return None
    interpreter = first_line[2:].split(" ", 1)[0]
    try:
        result = run_process(
            interpreter,
            ("-c", _PACKAGE_FILE_SCRIPT),
            options=ProcessRunOptions(timeout=5),
        )
    except OSError:
        return None
    if not result.succeeded:
        return None
    resolved = result.stdout.strip()
    return Path(resolved) if resolved else None


def detect_existing_ralph(
    *,
    which_fn: Callable[[str], str | None],
    environ: Mapping[str, str],
    resolve_package_file: Callable[[str], Path | None],
    path_exists: Callable[[Path], bool] = Path.exists,
) -> ExistingInstall | None:
    """Return the global ``ralph`` install, resolving its package location first."""
    executable = which_fn("ralph")
    if executable is None:
        return None
    package_file = resolve_package_file(executable)
    if package_file is None:
        return None
    info = detect_install(
        package_file=str(package_file),
        environ=environ,
        is_frozen=False,
        path_exists=path_exists,
    )
    return ExistingInstall(Path(executable), package_file, info.kind)


def prompt_for_conflict(
    existing: ExistingInstall,
    *,
    input_fn: Callable[[str], str],
    is_tty: bool,
) -> ConflictResolution:
    """Ask how to handle an existing install; never block without a TTY."""
    if not is_tty:
        raise RuntimeError(
            f"Existing {existing.kind} Ralph install at {existing.executable}; "
            "refusing to shadow it in non-interactive mode."
        )
    if existing.kind is InstallKind.SOURCE:
        return ConflictResolution.ABORT
    options = "[c]ontinue/[a]bort"
    if existing.remove_command is not None:
        options = "[c]ontinue/[r]emove/[a]bort"
    answer = input_fn(f"Existing {existing.kind} Ralph install at {existing.executable}; {options}: ")
    normalized = answer.strip().lower()
    if normalized in {"c", "continue"}:
        return ConflictResolution.CONTINUE
    if normalized in {"r", "remove"} and existing.remove_command is not None:
        return ConflictResolution.REMOVE
    return ConflictResolution.ABORT


def real_environment() -> Mapping[str, str]:
    """Return the real environment through a small injectable boundary."""
    return process_environ
