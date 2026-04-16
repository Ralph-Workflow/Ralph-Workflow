"""Tests for the installation/update workflow."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph import install as install_module

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest


def test_install_current_checkout_runs_pip_and_pipx(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    package_dir = Path("/tmp/ralph-python")

    install_module.install_current_checkout(
        package_dir=package_dir,
        run=fake_run,
        python_executable="/usr/bin/python3",
        pipx_executable="/usr/local/bin/pipx",
    )

    assert commands == [
        (("/usr/bin/python3", "-m", "pip", "install", "-e", ".[dev]"), package_dir),
        (
            (
                "/usr/local/bin/pipx",
                "install",
                "--force",
                "--editable",
                str(package_dir),
            ),
            package_dir,
        ),
    ]


def test_install_current_checkout_skips_pipx_when_not_available() -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    package_dir = Path("/tmp/ralph-python")

    install_module.install_current_checkout(
        package_dir=package_dir,
        run=fake_run,
        python_executable=sys.executable,
        pipx_executable=None,
    )

    assert commands == [
        ((sys.executable, "-m", "pip", "install", "-e", ".[dev]"), package_dir),
    ]


def test_main_uses_repo_directory_and_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_install_current_checkout(
        *,
        package_dir: Path,
        run: object,
        python_executable: str,
        pipx_executable: str | None,
    ) -> None:
        captured["package_dir"] = package_dir
        captured["python_executable"] = python_executable
        captured["pipx_executable"] = pipx_executable

    monkeypatch.setattr(install_module, "install_current_checkout", fake_install_current_checkout)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")

    assert install_module.main() == 0
    assert captured == {
        "package_dir": Path(install_module.__file__).resolve().parents[1],
        "python_executable": sys.executable,
        "pipx_executable": "/opt/bin/pipx",
    }
