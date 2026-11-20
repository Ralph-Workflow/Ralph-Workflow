from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ralph.skills import state_store
from tests._real_config_guard_support import (
    _REAL_HOME,
    _REAL_XDG_CONFIG_HOME,
    _clear_recorded_write_violations,
    _ConfigPathSnapshot,
    _diff_config_snapshots,
    _scan_guarded_config_files,
)

pytest_plugins = ("pytester",)

_CAPABILITY_STATE_FILE_NAME = "ralph-workflow-capabilities.json"


def test_capability_state_writer_targets_only_the_isolated_session_home(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    base_temp = Path(str(tmp_path_factory.getbasetemp()))
    isolated_home = Path.home()

    resolved = state_store.default_state_path()

    assert resolved == state_store.DEFAULT_STATE_PATH
    assert resolved.name == _CAPABILITY_STATE_FILE_NAME
    assert resolved.is_relative_to(isolated_home)
    assert isolated_home.is_relative_to(base_temp)
    assert not resolved.is_relative_to(_REAL_XDG_CONFIG_HOME)
    assert not resolved.is_relative_to(_REAL_HOME / ".config")

    state = state_store.load_capability_state()
    state_store.save_capability_state(state)

    assert resolved.is_file()
    assert resolved.read_text(encoding="utf-8") == state.model_dump_json(indent=2)


def test_guard_keeps_reporting_an_in_process_write_to_the_capability_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guarded = tmp_path / "guarded_config"
    guarded.mkdir()
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded))
    _clear_recorded_write_violations()

    target = guarded / _CAPABILITY_STATE_FILE_NAME
    target.write_text("{}\n", encoding="utf-8")
    before = _scan_guarded_config_files([guarded])

    target.write_text('{"skills": {}}\n', encoding="utf-8")
    after = _scan_guarded_config_files([guarded])

    violations = _diff_config_snapshots(before, after)

    assert any(
        "MODIFIED (content hash changed)" in violation and str(target.resolve()) in violation
        for violation in violations
    )
    _clear_recorded_write_violations()


def test_guard_ignores_only_unattributed_changes_to_the_capability_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guarded = tmp_path / "guarded_config"
    guarded.mkdir()
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded))
    _clear_recorded_write_violations()

    capability_state = guarded / _CAPABILITY_STATE_FILE_NAME
    other_guarded_file = guarded / "ralph-workflow-other.toml"

    def snapshot(path: Path, sha256: str, mtime_ns: int) -> _ConfigPathSnapshot:
        return _ConfigPathSnapshot(
            path=path.resolve(),
            is_dir=False,
            mtime_ns=mtime_ns,
            size=1,
            sha256=sha256,
        )

    capability_before = {capability_state.resolve(): snapshot(capability_state, "before", 1)}
    capability_after = {capability_state.resolve(): snapshot(capability_state, "after", 2)}
    other_before = {other_guarded_file.resolve(): snapshot(other_guarded_file, "before", 1)}
    other_after = {other_guarded_file.resolve(): snapshot(other_guarded_file, "after", 2)}

    assert _diff_config_snapshots(capability_before, capability_after) == []
    assert any(
        "MODIFIED (content hash changed)" in violation
        for violation in _diff_config_snapshots(other_before, other_after)
    )


def test_in_process_write_to_the_capability_state_file_still_fails_the_guard(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded = tmp_path / "guarded_config"
    guarded.mkdir()
    capabilities = guarded / _CAPABILITY_STATE_FILE_NAME
    capabilities.write_text('{"skills": {}}\n', encoding="utf-8")
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded))
    _clear_recorded_write_violations()

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_in_process_write=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_in_process_write_to_the_capability_state_file():
                Path({str(capabilities)!r}).write_text("tampered", encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert "ralph-workflow-capabilities.json" in output
    assert "MODIFIED (content hash changed)" in output


def test_external_process_rewrite_of_the_capability_state_file_raises_no_teardown_error(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded = tmp_path / "guarded_config"
    guarded.mkdir()
    capabilities = guarded / _CAPABILITY_STATE_FILE_NAME
    capabilities.write_text('{"skills": {}}\n', encoding="utf-8")
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded))
    _clear_recorded_write_violations()

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files

            pytest_plugins = ("pytester",)
            """
        )
    )
    pytester.makepyfile(
        test_external_rewrite=textwrap.dedent(
            f"""
            def test_external_process_rewrites_the_capability_state_file(pytester):
                writer = pytester.path / "external_writer.py"
                writer.write_text(
                    "open({str(capabilities)!r}, 'w').write('rewritten')\\n",
                    encoding="utf-8",
                )
                result = pytester.runpython(writer)
                assert result.ret == 0
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=0)
    assert "ralph-workflow-capabilities.json" not in result.stdout.str()
