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
        f"""#!{sys.executable}
import os
import re
import sys
import ast
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("uv 0.7.0")
    raise SystemExit(0)
if args == ["run", "--locked", "--project", ".", "python", "-m", "ralph.install", "--build"]:
    os.execv({sys.executable!r}, [{sys.executable!r}, "-m", "ralph.install", "--build"])
if len(args) == 2 and args == ["lock", "--check"]:
    raise SystemExit(0)
if args in (["sync", "--locked", "--extra", "dev"], ["sync", "--locked", "--extra", "dev", "--check"]):
    if os.environ.get("RALPH_FAKE_UV_FAIL_SYNC") == "1":
        print("offline fake uv injected sync failure", file=sys.stderr)
        raise SystemExit(17)
    venv = Path.cwd() / ".venv"
    marker = venv / "pyvenv.cfg"
    interpreter = venv / "bin" / "python"
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("home = offline fake uv\\n", encoding="utf-8")
    interpreter.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    interpreter.chmod(0o755)
    raise SystemExit(0)
if len(args) == 6 and args[0:3] == ["run", "--locked", "--project"] and args[4:] == ["ralph", "--version"]:
    project = Path(args[3])
    marker = project / ".venv" / "pyvenv.cfg"
    interpreter = project / ".venv" / "bin" / "python"
    if not marker.is_file() or not os.access(interpreter, os.X_OK):
        print("snapshot is missing the required fake uv environment", file=sys.stderr)
        raise SystemExit(19)
    package_dir = project / "ralph"
    version_source = (package_dir / "__init__.py").read_text(encoding="utf-8")
    flavor_source = (package_dir / "_build_meta.py").read_text(encoding="utf-8")
    version = re.search(r'^__version__(?:: str)? = "([^"]+)"', version_source, re.MULTILINE)
    flavor = re.search(r'^BUILD_FLAVOR(?:: str)? = (.+)$$', flavor_source, re.MULTILINE)
    if version is None or flavor is None:
        print("snapshot has no version", file=sys.stderr)
        raise SystemExit(18)
    print(version.group(1) + ast.literal_eval(flavor.group(1)))
    raise SystemExit(0)
print(f"offline fake uv rejected argv: {{args!r}}", file=sys.stderr)
raise SystemExit(64)
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
