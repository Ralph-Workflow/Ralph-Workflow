from __future__ import annotations

import textwrap
from collections.abc import Generator
from pathlib import Path

import pytest

from tests._real_config_guard_support import (
    _clear_recorded_read_violations,
    _clear_recorded_write_violations,
    _extract_read_path,
)

pytest_plugins = ("pytester",)
pytestmark = pytest.mark.timeout_seconds(10)


@pytest.fixture(autouse=True)
def _clean_violations() -> Generator[None, None, None]:
    try:
        yield
    finally:
        _clear_recorded_read_violations()
        _clear_recorded_write_violations()


def test_real_config_guard_catches_relative_read_after_chdir_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    safe_dir = tmp_path / "safe_dir"
    safe_dir.mkdir(parents=True)
    safe_file = safe_dir / "safe_data.txt"
    safe_file.write_text("safe content\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_relative=textwrap.dedent(
            f"""
            import os
            from pathlib import Path

            def test_safe_relative_read():
                orig = os.getcwd()
                try:
                    os.chdir({str(safe_dir)!r})
                    _ = Path("safe_data.txt").read_text(encoding="utf-8")
                finally:
                    os.chdir(orig)

            def test_deliberate_relative_read_in_guarded_dir():
                orig = os.getcwd()
                try:
                    os.chdir({str(guarded_dir)!r})
                    _ = Path("ralph-workflow.toml").read_text(encoding="utf-8")
                finally:
                    os.chdir(orig)
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=2, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "READ:" in output
    assert "ralph-workflow.toml" in output
    assert "safe_data.txt" not in output


def test_extract_read_path_handles_relative_path_after_chdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    target = guarded_dir / "ralph-workflow.toml"
    target.write_text("key = 1\n", encoding="utf-8")

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True)
    outside_file = outside_dir / "safe.txt"
    outside_file.write_text("safe\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    monkeypatch.chdir(outside_dir)
    assert _extract_read_path(("safe.txt", "r")) is None

    monkeypatch.chdir(guarded_dir)
    assert _extract_read_path(("ralph-workflow.toml", "r")) == target.resolve()
    assert _extract_read_path(("./ralph-workflow.toml", "r")) == target.resolve()


def test_default_production_guard_catches_relative_read_after_chdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "fake_home"
    fake_config = fake_home / ".config"
    fake_config.mkdir(parents=True)
    config_file = fake_config / "ralph-workflow.toml"
    config_file.write_text("k = 1\n", encoding="utf-8")

    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_HOME", fake_home)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_XDG_CONFIG_HOME", fake_config)

    monkeypatch.chdir(fake_config)
    assert _extract_read_path(("ralph-workflow.toml", "r")) == config_file.resolve()
    assert _extract_read_path(("./ralph-workflow.toml", "r")) == config_file.resolve()
