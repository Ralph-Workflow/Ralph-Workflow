from __future__ import annotations

import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from packaging.version import InvalidVersion, Version

from ralph._install_errors import InstallError
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.process._spawn_env import installer_env_for_spawn

if TYPE_CHECKING:
    from collections.abc import Sequence

_INSTALL_COMMAND_TIMEOUT_SECONDS = 60.0
_MINIMUM_UV_VERSION = Version("0.7.0")
_UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"
_UV_VERSION_FIELDS = 2
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


class _RunCommand(Protocol):
    def __call__(self, command: Sequence[str], *, cwd: Path) -> None: ...


class _BuildMetaWriter(Protocol):
    def __call__(
        self,
        package_dir: Path,
        flavor: str,
        *,
        source_commit: str = "",
        source_path: str = "",
        installed_at: str = "",
    ) -> None: ...


class _UvPreflight(Protocol):
    def __call__(self, uv_executable: str) -> None: ...


class _VersionRunner(Protocol):
    def __call__(self, uv_executable: str, snapshot: Path) -> str: ...


def run_command(command: Sequence[str], *, cwd: Path) -> None:
    cmd = tuple(command)
    result = run_process(
        cmd[0],
        cmd[1:],
        options=ProcessRunOptions(
            cwd=cwd, timeout=_INSTALL_COMMAND_TIMEOUT_SECONDS, env=installer_env_for_spawn()
        ),
    )
    if not result.succeeded:
        raise ProcessExecutionError(
            cmd, f"Command failed with exit code {result.returncode}: {' '.join(cmd)}"
        )


def preflight_uv(uv_executable: str) -> None:
    result = run_process(
        uv_executable,
        ("--version",),
        options=ProcessRunOptions(timeout=5.0, capture_output=True, env=installer_env_for_spawn()),
    )
    if not result.succeeded:
        raise InstallError(f"uv >= 0.7.0 is required; install it: {_UV_INSTALL_URL}")
    fields = result.stdout.strip().split()
    try:
        version = (
            Version(fields[1]) if len(fields) >= _UV_VERSION_FIELDS and fields[0] == "uv" else None
        )
    except InvalidVersion as error:
        raise InstallError(f"uv >= 0.7.0 is required; install it: {_UV_INSTALL_URL}") from error
    if version is None or version < _MINIMUM_UV_VERSION:
        raise InstallError(f"uv >= 0.7.0 is required; install it: {_UV_INSTALL_URL}")


def run_snapshot_version(uv_executable: str, snapshot: Path) -> str:
    result = run_process(
        uv_executable,
        ("run", "--locked", "--project", str(snapshot), "ralph", "--version"),
        options=ProcessRunOptions(
            cwd=snapshot,
            timeout=_INSTALL_COMMAND_TIMEOUT_SECONDS,
            capture_output=True,
            env=installer_env_for_spawn(),
        ),
    )
    if not result.succeeded:
        raise ProcessExecutionError(
            result.command,
            f"Command failed with exit code {result.returncode}: {' '.join(result.command)}",
        )
    return result.stdout


def validate_snapshot_version(
    run_version: _VersionRunner, uv_executable: str, snapshot: Path, flavor: str
) -> None:
    version_output = strip_terminal_control(run_version(uv_executable, snapshot)).strip()
    if flavor not in version_output:
        raise InstallError(f"Installed snapshot did not report required {flavor} version suffix.")


def write_dev_launcher(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    staging = Path(staging_name)
    backup: Path | None = None
    replaced = False
    try:
        with os.fdopen(  # filesystem-write-ok: exclusive launcher staging must be durable before replacement.
            descriptor, "w", encoding="utf-8"
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())  # filesystem-write-ok: durable launcher staging before replacement.
        staging.chmod(0o755)  # filesystem-write-ok: launcher must be executable before atomic publication.
        if path.exists() or path.is_symlink():
            backup_descriptor, backup_name = tempfile.mkstemp(
                prefix=f".{path.name}.", dir=path.parent, text=False
            )
            os.close(backup_descriptor)
            backup = Path(backup_name)
            path.replace(backup)  # filesystem-write-ok: preserve the prior launcher without following it.
        staging.replace(path)  # filesystem-write-ok: atomically publish fully prepared launcher staging.
        replaced = True
        _fsync_directory(path.parent)
        if backup is not None:
            backup.unlink()  # filesystem-write-ok: discard the superseded launcher after durable publication.
    except OSError as error:
        if replaced:
            try:
                if backup is None:
                    path.unlink()  # filesystem-write-ok: undo an uncommitted first-install launcher.
                else:
                    backup.replace(path)  # filesystem-write-ok: atomically restore the prior launcher.
                _fsync_directory(path.parent)
            except OSError as recovery_error:
                raise InstallError(
                    f"launcher publication failed and recovery was not durable: {recovery_error}"
                ) from error
        elif backup is not None:
            backup.replace(path)  # filesystem-write-ok: restore prior launcher after replacement failure.
            _fsync_directory(path.parent)
        raise
    finally:
        staging.unlink(missing_ok=True)  # filesystem-write-ok: discard failed exclusive launcher staging.


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(  # filesystem-write-ok: fsync the directory containing atomic launcher replacement.
        directory, os.O_RDONLY | os.O_DIRECTORY
    )
    try:
        os.fsync(descriptor)  # filesystem-write-ok: persist atomic launcher replacement directory entry.
    finally:
        os.close(descriptor)


def utc_now_iso8601() -> str:
    return datetime.now(UTC).isoformat()


def resolve_source_commit(source_dir: Path) -> str:
    try:
        result = run_process(
            "git", ("rev-parse", "HEAD"), options=ProcessRunOptions(cwd=source_dir, timeout=5.0)
        )
    except (OSError, ProcessExecutionError):
        return ""
    return result.stdout.strip() if result.succeeded else ""


def write_build_flavor(
    package_dir: Path,
    flavor: str,
    *,
    source_commit: str = "",
    source_path: str = "",
    installed_at: str = "",
) -> None:
    build_meta = package_dir / "ralph" / "_build_meta.py"
    content = build_meta.read_text(encoding="utf-8")
    updated_content = _replace_build_metadata(content, "BUILD_FLAVOR", flavor)
    updated_content = _replace_build_metadata(updated_content, "BUILD_SOURCE_COMMIT", source_commit)
    updated_content = _replace_build_metadata(updated_content, "BUILD_SOURCE_PATH", source_path)
    updated_content = _replace_build_metadata(updated_content, "BUILD_INSTALLED_AT", installed_at)
    write_text_if_changed(DEFAULT_FILE_BACKEND, build_meta, updated_content, encoding="utf-8")


def _replace_build_metadata(content: str, name: str, value: str) -> str:
    literal = repr(value)
    if name == "BUILD_SOURCE_COMMIT" and _GIT_COMMIT_PATTERN.fullmatch(value) is None:
        literal = repr("")
    pattern = re.compile(rf"^{re.escape(name)}(?:: str)?\s*=\s*.*$", re.MULTILINE)
    replacement = f"{name}: str = {literal}".replace("\\", r"\\")
    return pattern.sub(replacement, content)
