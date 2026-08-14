"""Black-box regression coverage for packaging gate behavior."""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess_e2e

PACKAGE_ROOT = Path(__file__).parent.parent


def _make_command_path() -> str:
    make = shutil.which("make")
    assert make is not None
    return make


def _isolated_path(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("bash", "dirname"):
        command_path = shutil.which(command)
        assert command_path is not None
        (bin_dir / command).symlink_to(command_path)
    return bin_dir


def _run_formula_check(bin_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_make_command_path(), "formula-check"],
        cwd=PACKAGE_ROOT,
        env={"PATH": str(bin_dir)},
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )


def _write_fake_ruby(bin_dir: Path, *, exit_code: int) -> None:
    # The shebang is resolved from the real bash on PATH: a hardcoded
    # ``#!/usr/bin/bash`` would bypass the isolated PATH entirely (shebang
    # paths are absolute, never PATH-resolved) and fail on hosts whose bash
    # lives elsewhere (e.g. ``/bin/bash`` on macOS).
    bash_path = shutil.which("bash")
    assert bash_path is not None
    ruby = bin_dir / "ruby"
    formula = PACKAGE_ROOT / "Formula" / "ralph-workflow.rb"
    ruby.write_text(
        f"#!{bash_path}\n"
        f"if [ \"$1\" != \"-c\" ] || [ \"$2\" != \"{formula}\" ]; then\n"
        "  exit 9\n"
        "fi\n"
        f"printf 'fake ruby syntax check\\n'\nexit {exit_code}\n"
    )
    ruby.chmod(ruby.stat().st_mode | stat.S_IXUSR)


def test_formula_check_fails_closed_when_ruby_is_unavailable(tmp_path: Path) -> None:
    result = _run_formula_check(_isolated_path(tmp_path))

    assert result.returncode != 0
    assert "Ruby is required for formula-check" in result.stderr
    assert "docs/ralph-workflow-policy/gate-script-policy.md" in result.stderr


def test_formula_check_propagates_ruby_syntax_result(tmp_path: Path) -> None:
    bin_dir = _isolated_path(tmp_path)
    _write_fake_ruby(bin_dir, exit_code=0)

    result = _run_formula_check(bin_dir)

    assert result.returncode == 0
    assert "fake ruby syntax check" in result.stdout


def test_formula_check_fails_when_ruby_rejects_formula(tmp_path: Path) -> None:
    bin_dir = _isolated_path(tmp_path)
    _write_fake_ruby(bin_dir, exit_code=1)

    result = _run_formula_check(bin_dir)

    assert result.returncode != 0
    assert "fake ruby syntax check" in result.stdout
