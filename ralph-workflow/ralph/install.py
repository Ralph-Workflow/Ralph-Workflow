"""Public checkout-installer API and module command entry point."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ralph import _BASE_VERSION
from ralph._install_conflicts import (
    ConflictResolution,
    detect_existing_ralph,
    prompt_for_conflict,
    real_environment,
    resolve_package_file,
)
from ralph._install_copy_tree import SnapshotIdentity, copy_install_tree, read_snapshot_identity
from ralph._install_errors import InstallError
from ralph._install_render import (
    DEV_LAUNCHER_NAME,
    render_dev_launcher,
    render_dev_launcher_notice,
    render_install_summary,
)
from ralph._install_runtime import (
    _BuildMetaWriter,
    _RunCommand,
    _UvPreflight,
    _VersionRunner,
    preflight_uv,
    resolve_source_commit,
    run_command,
    run_snapshot_version,
    utc_now_iso8601,
    validate_snapshot_version,
    write_build_flavor,
    write_dev_launcher,
)
from ralph.install_transaction import (
    cleanup_staging,
    create_candidate,
    install_lock,
    point_current,
    publish_generation,
)
from ralph.process._spawn_env import sanitize_process_environment

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

STABLE_PACKAGE_NAME = "ralph-workflow"
_UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"


class LauncherWriter(Protocol):
    """Publishes the generated global dev launcher."""

    def __call__(self, path: Path, content: str) -> None: ...


class InstallLockFactory(Protocol):
    """Creates the transaction lock for a shared install root."""

    def __call__(self, path: Path) -> AbstractContextManager[None]: ...


class GenerationPublisher(Protocol):
    """Finalize a candidate generation and publish the launcher while holding the install lock."""

    def __call__(
        self, candidate: Path, generations: Path, *, publish_launcher: Callable[[Path], None]
    ) -> Path: ...


def _publish_launcher(
    generation: Path,
    launcher_path: Path,
    write_launcher: LauncherWriter,
    uv_executable: str,
) -> None:
    write_launcher(launcher_path, render_dev_launcher(generation, uv_executable))


def install_dev_checkout(
    *,
    run: _RunCommand = run_command,
    uv_executable: str | None,
    cwd: Path,
    launcher_dir: Path,
    install_root: Path | None = None,
    copy_tree: Callable[[Path, Path], Path] = copy_install_tree,
    write_flavor: _BuildMetaWriter = write_build_flavor,
    resolve_commit: Callable[[Path], str] = resolve_source_commit,
    installed_at: Callable[[], str] = utc_now_iso8601,
    write_launcher: LauncherWriter = write_dev_launcher,
    read_identity: Callable[[Path], SnapshotIdentity | None] = read_snapshot_identity,
    emit: Callable[[str], None] = print,
    flavor: str = "-dev",
    preflight: _UvPreflight = preflight_uv,
    run_version: _VersionRunner = run_snapshot_version,
    lock: InstallLockFactory = install_lock,
    candidate_factory: Callable[[Path], Path] = create_candidate,
    publish: GenerationPublisher = publish_generation,
    update_current: Callable[[Path, Path], None] = point_current,
) -> None:
    """Build, validate, and transactionally publish a checkout snapshot."""
    if uv_executable is None:
        raise InstallError(f"uv >= 0.7.0 is required; install it: {_UV_INSTALL_URL}")
    root = install_root or Path.home() / ".local" / "share" / "ralph-workflow-dev"
    staging, generations, current = root / ".staging", root / "generations", root / "current"
    preflight(uv_executable)
    with lock(root / ".install.lock"):
        cleanup_staging(staging)
        completed = False
        try:
            replaced = read_identity(current)
            candidate = candidate_factory(staging)
            copied_dir = copy_tree(cwd, candidate)
            source_commit = resolve_commit(cwd)
            run((uv_executable, "lock", "--check"), cwd=copied_dir)
            write_flavor(
                copied_dir,
                flavor,
                source_commit=source_commit,
                source_path=str(cwd),
                installed_at=installed_at(),
            )
            run((uv_executable, "sync", "--locked", "--extra", "dev"), cwd=copied_dir)
            run((uv_executable, "sync", "--locked", "--extra", "dev", "--check"), cwd=copied_dir)
            validate_snapshot_version(run_version, uv_executable, copied_dir, flavor)
            launcher_path = launcher_dir / DEV_LAUNCHER_NAME
            generation = publish(
                copied_dir,
                generations,
                publish_launcher=lambda finalized: _publish_launcher(
                    finalized, launcher_path, write_launcher, uv_executable
                ),
            )
            update_current(current, generation)
            completed = True
        finally:
            if not completed:
                cleanup_staging(staging)
        emit(
            render_install_summary(
                source=cwd,
                commit=source_commit,
                version=_BASE_VERSION + flavor,
                snapshot=generation,
                launcher=launcher_path,
                replaced=replaced,
            )
        )


def install_stable_release(
    *,
    run: _RunCommand = run_command,
    uv_executable: str | None,
    cwd: Path,
    version: str | None = None,
    from_path: Path | None = None,
    which_fn: Callable[[str], str | None] = shutil.which,
    resolve_installed_package_file: Callable[[str], Path | None] = resolve_package_file,
    write_flavor: _BuildMetaWriter = write_build_flavor,
    resolve_commit: Callable[[Path], str] = resolve_source_commit,
    installed_at: Callable[[], str] = utc_now_iso8601,
    preflight: _UvPreflight = preflight_uv,
) -> None:
    """Install the requested release with uv tool."""
    if uv_executable is None:
        raise InstallError(f"uv >= 0.7.0 is required; install it: {_UV_INSTALL_URL}")
    preflight(uv_executable)
    command = [uv_executable, "tool", "install", "--force"]
    if from_path is not None:
        command.append(str(from_path))
    elif version is None:
        command.extend(("--upgrade", STABLE_PACKAGE_NAME))
    else:
        command.append(f"{STABLE_PACKAGE_NAME}=={version}")
    run(tuple(command), cwd=cwd)
    if from_path is not None:
        installed_package_file = resolve_installed_package_file(which_fn("ralph") or "")
        if installed_package_file is None:
            raise InstallError(
                "Installed ralph package could not be located to mark it as a manual build."
            )
        write_flavor(
            installed_package_file.parents[1],
            "-build",
            source_commit=resolve_commit(cwd),
            installed_at=installed_at(),
        )


class _InstallArgs(argparse.Namespace):
    stable: bool
    build: bool
    version: str | None
    from_path: Path | None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ralph.install",
        description="Install Ralph Workflow as a dev build (default) or a pinned stable build.",
    )
    parser.add_argument("--stable", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--version", default=None)
    parser.add_argument("--from", dest="from_path", type=Path, default=None)
    return parser


def _parse_args(argv: Sequence[str] | None) -> tuple[bool, bool, str | None, Path | None]:
    parsed = _InstallArgs()
    _build_parser().parse_args(argv, namespace=parsed)
    return parsed.stable, parsed.build, parsed.version, parsed.from_path


def main(argv: Sequence[str] | None = None) -> int:
    """Run the installer module command."""
    sanitize_process_environment()
    stable, build, version, from_path = _parse_args(argv)
    package_dir = Path(__file__).resolve().parents[1]
    if stable or version is not None or from_path is not None:
        uv_executable = _absolute_uv_path(shutil.which("uv"))
        if uv_executable is None:
            raise InstallError(f"uv >= 0.7.0 is required; install it: {_UV_INSTALL_URL}")
        preflight_uv(uv_executable)
        _resolve_install_conflict(run=run_command)
        install_stable_release(
            run=run_command,
            uv_executable=uv_executable,
            cwd=package_dir,
            version=version,
            from_path=from_path,
        )
    else:
        _report_dev_launcher_conflict()
        install_dev_checkout(
            run=run_command,
            uv_executable=_absolute_uv_path(shutil.which("uv")),
            cwd=package_dir,
            launcher_dir=Path.home() / ".local" / "bin",
            flavor="-build" if build else "-dev",
        )
    return 0


def _absolute_uv_path(found: str | None) -> str | None:
    return None if found is None else str(Path(found).resolve(strict=True))


def _report_dev_launcher_conflict(*, emit: Callable[[str], None] = print) -> None:
    existing = detect_existing_ralph(
        which_fn=shutil.which,
        environ=real_environment(),
        resolve_package_file=resolve_package_file,
    )
    if existing is not None:
        emit(render_dev_launcher_notice(existing))


def _resolve_install_conflict(*, run: _RunCommand) -> None:
    existing = detect_existing_ralph(
        which_fn=shutil.which,
        environ=real_environment(),
        resolve_package_file=resolve_package_file,
    )
    if existing is None:
        return
    resolution = prompt_for_conflict(
        existing,
        input_fn=input,
        is_tty=os.isatty(0),
        cwd=Path(__file__).resolve().parents[1],
    )
    if resolution is ConflictResolution.ABORT:
        raise InstallError(f"Installation aborted because {existing.executable} already exists.")
    if resolution is ConflictResolution.REMOVE:
        command = existing.remove_command
        if command is None:
            raise InstallError(f"Cannot remove existing {existing.kind} install automatically.")
        run(command, cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())
