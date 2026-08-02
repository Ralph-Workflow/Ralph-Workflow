"""Conflict detection for the global ``ralph`` executable.

The update checker classifies package files, not console-script paths.  This
module resolves the executable to its owning package before asking it to classify
an existing install.
"""

from __future__ import annotations

from enum import StrEnum
from os import environ as process_environ
from pathlib import Path
from typing import TYPE_CHECKING

from ralph._existing_install import ExistingInstall
from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
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


def _interpreter_from_shebang(first_line: str) -> str | None:
    if not first_line.startswith("#!"):
        return None
    interpreter_parts = first_line[2:].split()
    if not interpreter_parts:
        return None
    interpreter = interpreter_parts[0]
    if Path(interpreter).name != "env":
        return interpreter
    interpreter_parts = interpreter_parts[1:]
    if interpreter_parts[:1] == ["-S"]:
        interpreter_parts = interpreter_parts[1:]
    return interpreter_parts[0] if interpreter_parts else None


def resolve_package_file(executable: str) -> Path | None:
    """Resolve a console script to its interpreter's installed package file."""
    script = Path(executable).resolve()
    try:
        # filesystem-read-ok: console-script shebang inspection occurs once per explicit install-conflict check
        first_line = script.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    interpreter = _interpreter_from_shebang(first_line)
    if interpreter is None:
        return None
    try:
        result = run_process(
            interpreter,
            ("-c", _PACKAGE_FILE_SCRIPT),
            options=ProcessRunOptions(timeout=5),
        )
    except ProcessExecutionError:
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
    answer = input_fn(
        f"Existing {existing.kind} Ralph install at {existing.executable}; {options}: "
    )
    normalized = answer.strip().lower()
    if normalized in {"c", "continue"}:
        return ConflictResolution.CONTINUE
    if normalized in {"r", "remove"} and existing.remove_command is not None:
        return ConflictResolution.REMOVE
    return ConflictResolution.ABORT


def real_environment() -> Mapping[str, str]:
    """Return the real environment through a small injectable boundary."""
    return process_environ
