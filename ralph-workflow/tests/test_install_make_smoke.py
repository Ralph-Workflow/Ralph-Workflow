"""Offline black-box coverage for the public ``make install`` command.

The fake ``uv`` accepts only installer argv forms and never reaches a network.
The source checkout's real Makefile and installer execute in isolated HOME and
PATH directories, so each assertion observes the installed artifacts exactly
as an operator would.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_COMMAND_TIMEOUT_SECONDS = 5.0


def _write_fake_uv(directory: Path) -> None:
    fake_uv = directory / "uv"
    fake_uv.write_text(
        f"""#!/bin/sh
set -eu

if [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then
    printf '%s\\n' 'uv 0.7.0'
elif [ "$#" -eq 8 ] && [ "$1" = "run" ] && [ "$2" = "--locked" ] && [ "$3" = "--project" ] && [ "$4" = "." ] && [ "$5" = "python" ] && [ "$6" = "-m" ] && [ "$7" = "ralph.install" ] && [ "$8" = "--build" ]; then
    exec {sys.executable!r} -m ralph.install --build
elif [ "$#" -eq 2 ] && [ "$1" = "lock" ] && [ "$2" = "--check" ]; then
    exit 0
elif [ "$#" -eq 4 ] && [ "$1" = "sync" ] && [ "$2" = "--locked" ] && [ "$3" = "--extra" ] && [ "$4" = "dev" ]; then
    if [ "${{RALPH_FAKE_UV_FAIL_SYNC:-0}}" = "1" ]; then
        printf '%s\\n' 'offline fake uv injected sync failure' >&2
        exit 17
    fi
    mkdir -p .venv/bin
    printf '%s\\n' 'home = offline fake uv' > .venv/pyvenv.cfg
    printf '%s\\n' '#!/bin/sh' 'exit 0' > .venv/bin/python
    chmod 755 .venv/bin/python
elif [ "$#" -eq 5 ] && [ "$1" = "sync" ] && [ "$2" = "--locked" ] && [ "$3" = "--extra" ] && [ "$4" = "dev" ] && [ "$5" = "--check" ]; then
    if [ "${{RALPH_FAKE_UV_FAIL_SYNC:-0}}" = "1" ]; then
        printf '%s\\n' 'offline fake uv injected sync failure' >&2
        exit 17
    fi
    mkdir -p .venv/bin
    printf '%s\\n' 'home = offline fake uv' > .venv/pyvenv.cfg
    printf '%s\\n' '#!/bin/sh' 'exit 0' > .venv/bin/python
    chmod 755 .venv/bin/python
elif [ "$#" -eq 6 ] && [ "$1" = "run" ] && [ "$2" = "--locked" ] && [ "$3" = "--project" ] && [ "$5" = "ralph" ] && [ "$6" = "--version" ]; then
    project=$4
    if [ ! -f "$project/.venv/pyvenv.cfg" ] || [ ! -x "$project/.venv/bin/python" ]; then
        printf '%s\\n' 'snapshot is missing the required fake uv environment' >&2
        exit 19
    fi
    version=$(sed -En 's/^__version__(: str)? = "([^"]*)"$/\\2/p' "$project/ralph/__init__.py")
    flavor=$(grep -E '^BUILD_FLAVOR(: str)? = ' "$project/ralph/_build_meta.py" | sed 's/.*= //' | tr -d '"' | tr -d "'")
    if [ -z "$version" ] || ! grep -Eq '^BUILD_FLAVOR(: str)? = ' "$project/ralph/_build_meta.py"; then
        printf '%s\\n' 'snapshot has no version' >&2
        exit 18
    fi
    printf '%s%s\\n' "$version" "$flavor"
else
    printf 'offline fake uv rejected argv: '
    printf '%s ' "$@"
    printf '%s\\n' >&2
    exit 64
fi
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)


def _copy_install_fixture(source: Path, destination: Path) -> None:
    excluded = frozenset(
        {
            ".agent",
            ".git",
            ".hypothesis",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "docs",
            "tests",
            "tmp",
        }
    )

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & excluded

    shutil.copytree(source, destination, ignore=ignore)


def _environment(home: Path, fake_bin: Path) -> dict[str, str]:
    return {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{home / '.local' / 'bin'}:{os.defpath}",
        "RALPH_FAKE_UV_FAIL_SYNC": "0",
    }


def _run_make_install(source: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("make", "install"),
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )


def _run_rdev(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("rdev", "--version"),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )


def _assert_rdev_requires_generation_venv(environment: dict[str, str], generation: Path) -> None:
    generation_venv = generation / ".venv"
    missing_venv = generation / ".venv.missing"
    generation_venv.replace(missing_venv)
    try:
        missing_environment = _run_rdev(environment)
    finally:
        missing_venv.replace(generation_venv)
    assert missing_environment.returncode != 0
    assert "required fake uv environment" in missing_environment.stderr


def _assert_generation_has_fake_uv_environment(generation: Path) -> None:
    assert (generation / ".venv" / "pyvenv.cfg").is_file()
    assert os.access(generation / ".venv" / "bin" / "python", os.X_OK)


def test_make_install_regression_offline_transaction_preserves_operator_artifacts(
    tmp_path: Path,
) -> None:
    """The public install command is offline, transactional, and reinstall-safe."""
    source = tmp_path / "source"
    _copy_install_fixture(Path(__file__).resolve().parents[1], source)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_uv(fake_bin)
    environment = _environment(home, fake_bin)
    root = home / ".local" / "share" / "ralph-workflow-dev"
    current = root / "current"
    launcher = home / ".local" / "bin" / "rdev"

    missing_uv = _run_make_install(source, {**environment, "PATH": os.defpath})
    assert missing_uv.returncode != 0
    assert "uv >= 0.7.0 is required" in missing_uv.stderr

    first_install = _run_make_install(source, environment)
    assert first_install.returncode == 0, first_install.stderr
    assert launcher.is_file()
    assert current.is_symlink()
    first_generation = current.resolve()
    assert first_generation.parent == root / "generations"
    assert (first_generation / "ralph" / "__init__.py").is_file()
    _assert_generation_has_fake_uv_environment(first_generation)
    _assert_rdev_requires_generation_venv(environment, first_generation)

    source_backup = source.with_name(f"{source.name}.installer-smoke-backup")
    source.replace(source_backup)
    try:
        detached_source_version = _run_rdev(environment)
    finally:
        source_backup.replace(source)
    assert detached_source_version.returncode == 0, detached_source_version.stderr
    assert detached_source_version.stdout.strip().endswith("-build")
