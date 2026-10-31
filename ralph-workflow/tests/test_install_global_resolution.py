"""The installer must resolve global tools, never its own ``uv run`` virtualenv.

``make install`` / ``python -m ralph.install`` run through ``uv run``, which puts
the checkout's ``.venv/bin`` first on ``PATH``. A bare ``shutil.which`` there
finds the checkout's editable ``ralph`` and the dev-extra ``uv``:

* ``--from <wheel>`` then rewrote the checkout's own ``ralph/_build_meta.py``
  with the ``-build`` flavor instead of marking the uv-tool install, and
* the ``rdev`` launcher hard-coded ``.venv/bin/uv``, which breaks as soon as
  that virtualenv is rebuilt or its volume is unmounted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ralph import install as install_module


def _executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def installer_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Put a fake installer virtualenv ``bin`` ahead of a fake global ``bin`` on PATH."""
    venv_bin = tmp_path / "checkout" / ".venv" / "bin"
    global_bin = tmp_path / "home" / ".local" / "bin"
    for name in ("ralph", "uv"):
        _executable(venv_bin, name)
        _executable(global_bin, name)
    monkeypatch.setattr(sys, "prefix", str(venv_bin.parent))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base-python"))
    monkeypatch.setenv("PATH", os.pathsep.join((str(venv_bin), str(global_bin))))
    return venv_bin, global_bin


def test_from_path_marks_the_global_install_not_the_installer_venv(
    installer_venv: tuple[Path, Path],
) -> None:
    """The ``-build`` flavor must land in the uv-tool install, not the checkout."""
    _venv_bin, global_bin = installer_venv
    looked_up: list[str] = []

    def resolve_package_file(executable: str) -> Path:
        looked_up.append(executable)
        return Path("/uv/tools/ralph-workflow/lib/site-packages/ralph/__init__.py")

    install_module.install_stable_release(
        run=lambda _command, *, cwd: None,
        uv_executable="uv",
        cwd=Path("/checkout"),
        from_path=Path("/checkout/dist/ralph.whl"),
        resolve_installed_package_file=resolve_package_file,
        write_flavor=lambda _package_dir, _flavor, **_kwargs: None,
        resolve_commit=lambda _cwd: "",
        preflight=lambda _uv: None,
    )

    assert looked_up == [str(global_bin / "ralph")]


def test_dev_launcher_uses_the_global_uv_not_the_installer_venv(
    installer_venv: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rdev`` must not depend on the checkout's ``.venv`` surviving."""
    _venv_bin, global_bin = installer_venv
    captured: dict[str, object] = {}
    monkeypatch.setattr(install_module, "detect_existing_ralph", lambda **_kwargs: None)
    monkeypatch.setattr(
        install_module, "install_dev_checkout", lambda **kwargs: captured.update(kwargs)
    )

    assert install_module.main([]) == 0

    assert captured["uv_executable"] == str((global_bin / "uv").resolve())


def test_stable_install_uses_the_global_uv_not_the_installer_venv(
    installer_venv: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stable path resolves ``uv`` the same way as the dev path."""
    _venv_bin, global_bin = installer_venv
    captured: dict[str, object] = {}
    preflighted: list[str] = []
    monkeypatch.setattr(install_module, "preflight_uv", preflighted.append)
    monkeypatch.setattr(install_module, "detect_existing_ralph", lambda **_kwargs: None)
    monkeypatch.setattr(
        install_module, "install_stable_release", lambda **kwargs: captured.update(kwargs)
    )

    assert install_module.main(["--stable"]) == 0

    assert preflighted == [str((global_bin / "uv").resolve())]
    assert captured["uv_executable"] == str((global_bin / "uv").resolve())
