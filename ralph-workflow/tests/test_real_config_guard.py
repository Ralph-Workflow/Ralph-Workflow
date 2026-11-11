from __future__ import annotations

import os
import textwrap
import tomllib
from pathlib import Path

import pytest

import ralph.config.loader as config_loader
from ralph.skills import state_store
from tests._real_config_guard_support import (
    _BUNDLED_AGENTS_TEMPLATE,
    _STATE,
    _clear_recorded_read_violations,
    _extract_guarded_path,
    _extract_read_path,
    _extract_write_path,
    _get_test_read_violations,
    _read_violations_count,
)
from tests.conftest import (
    _ConfigPathSnapshot,
    _diff_config_snapshots,
    _resolve_guarded_config_dirs,
    _scan_guarded_config_files,
)

pytest_plugins = ("pytester",)


def test_resolve_guarded_config_dirs_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    dirs = _resolve_guarded_config_dirs()
    assert len(dirs) >= 1
    dir_strings = [str(d) for d in dirs]
    assert any(".config" in s for s in dir_strings)


def test_resolve_guarded_config_dirs_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom_dir = tmp_path / "custom_config"
    custom_dir.mkdir()
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(custom_dir))
    dirs = _resolve_guarded_config_dirs()
    assert dirs == [custom_dir.resolve()]


def test_diff_config_snapshots_detection(tmp_path: Path) -> None:
    file_a = tmp_path / "ralph-workflow-a.toml"
    file_b = tmp_path / "ralph-workflow-b.toml"

    snap_a1 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1000, size=10, sha256="hash1")
    snap_b = _ConfigPathSnapshot(file_b, is_dir=False, mtime_ns=2000, size=20, sha256="hash2")

    assert _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a1}) == []

    diff_created = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a1, file_b: snap_b})
    assert len(diff_created) == 1
    assert f"CREATED: {file_b}" in diff_created[0]

    diff_deleted = _diff_config_snapshots({file_a: snap_a1, file_b: snap_b}, {file_a: snap_a1})
    assert len(diff_deleted) == 1
    assert f"DELETED: {file_b}" in diff_deleted[0]

    snap_a2 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1001, size=12, sha256="hash_mod")
    diff_hash = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a2})
    assert len(diff_hash) == 1
    assert f"MODIFIED (content hash changed): {file_a}" in diff_hash[0]

    snap_a3 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1002, size=10, sha256="hash1")
    diff_mtime = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a3})
    assert len(diff_mtime) == 1
    assert f"MODIFIED (mtime changed): {file_a}" in diff_mtime[0]

    snap_a4 = _ConfigPathSnapshot(
        file_a, is_dir=False, mtime_ns=1000, size=10, sha256="hash1", mode=0o644
    )
    snap_a5 = _ConfigPathSnapshot(
        file_a, is_dir=False, mtime_ns=1000, size=10, sha256="hash1", mode=0o600
    )
    diff_mode = _diff_config_snapshots({file_a: snap_a4}, {file_a: snap_a5})
    assert len(diff_mode) == 1
    assert f"MODIFIED (mode changed): {file_a}" in diff_mode[0]


def test_scan_guarded_config_files_scans_recursively(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    top_file = config_dir / "ralph-workflow.toml"
    top_file.write_text("key = 1\n", encoding="utf-8")

    sub_dir = config_dir / "ralph-workflow-data"
    sub_dir.mkdir()
    nested_file = sub_dir / "state.json"
    nested_file.write_text("{}", encoding="utf-8")

    unrelated = config_dir / "other.toml"
    unrelated.write_text("x = 1\n", encoding="utf-8")

    snapshot = _scan_guarded_config_files([config_dir])
    assert top_file.resolve() in snapshot
    assert not snapshot[top_file.resolve()].is_dir
    assert snapshot[top_file.resolve()].sha256 != ""

    assert sub_dir.resolve() in snapshot
    assert snapshot[sub_dir.resolve()].is_dir

    assert nested_file.resolve() in snapshot
    assert not snapshot[nested_file.resolve()].is_dir

    assert unrelated.resolve() not in snapshot


def test_real_config_guard_catches_created_file_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_leak=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_leak_creation():
                leaked = Path({str(guarded_dir)!r}) / "ralph-workflow-leak.toml"
                leaked.write_text("leaked_key = true\\n", encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "CREATED:" in output
    assert "ralph-workflow-leak.toml" in output


def test_real_config_guard_catches_modified_file_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_modify=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_modification():
                target = Path({str(existing_file)!r})
                target.write_text("tampered = 2\\n", encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "MODIFIED (content hash changed):" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_catches_deleted_file_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_delete=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_deletion():
                target = Path({str(existing_file)!r})
                target.unlink()
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "DELETED:" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_catches_chmod_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_chmod=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_chmod():
                target = Path({str(existing_file)!r})
                target.chmod(0o600)
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "MODIFIED (mode changed):" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_catches_read_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_read=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_read():
                target = Path({str(existing_file)!r})
                _ = target.read_text(encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "READ:" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_clean_suite_passes_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_clean=textwrap.dedent(
            """
            def test_clean_behavior():
                assert 1 + 1 == 2
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=0, failed=0)


def test_per_test_isolation_redirects_import_time_constants() -> None:
    home = Path.home()
    assert "autouse-home" in str(home)

    assert "autouse-home" in str(config_loader.GLOBAL_CONFIG_PATH)
    assert config_loader.GLOBAL_CONFIG_PATH.name == "ralph-workflow.toml"

    assert "autouse-home" in str(config_loader.GLOBAL_AGENTS_CONFIG_PATH)
    assert config_loader.GLOBAL_AGENTS_CONFIG_PATH.name == "ralph-workflow-agents.toml"

    assert "autouse-home" in str(state_store.DEFAULT_STATE_PATH)
    assert state_store.DEFAULT_STATE_PATH.name == "ralph-workflow-capabilities.json"


def test_autouse_isolation_registers_current_test_node(request: pytest.FixtureRequest) -> None:
    assert _STATE.current_test_node == request.node.nodeid


def test_read_violations_tracking_helpers(tmp_path: Path) -> None:
    start = _read_violations_count()
    node_a = "test_file.py::test_a"
    node_b = "test_file.py::test_b"
    path_1 = tmp_path / "ralph-workflow.toml"
    path_2 = tmp_path / "ralph-workflow-agents.toml"

    _STATE.recorded_reads.append((node_a, path_1))
    _STATE.recorded_reads.append((node_b, path_2))
    _STATE.recorded_reads.append((node_a, path_2))

    assert _read_violations_count() == start + 3
    assert _get_test_read_violations(start, node_a) == [path_1, path_2]
    assert _get_test_read_violations(start, node_b) == [path_2]
    assert _get_test_read_violations(start, "test_file.py::non_existent") == []

    _clear_recorded_read_violations()


def test_real_config_guard_attributes_read_violation_to_node_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            import pytest
            from tests.conftest import _guard_real_config_files, _set_current_test_node

            @pytest.fixture(autouse=True)
            def _track_node(request: pytest.FixtureRequest):
                _set_current_test_node(request.node.nodeid)
                try:
                    yield
                finally:
                    _set_current_test_node(None)
            """
        )
    )
    pytester.makepyfile(
        test_read=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_attributed_read():
                target = Path({str(existing_file)!r})
                _ = target.read_text(encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "READ:" in output
    assert "ralph-workflow.toml" in output
    assert "(by test_read.py::test_attributed_read)" in output


def test_real_config_guard_catches_write_agents_toml_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow-agents.toml"
    existing_file.write_text("[agents.claude]\ncmd = 'claude'\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_write_agents=textwrap.dedent(
            f"""
            from pathlib import Path
            from ralph.config.agents_file_writer import write_agents_toml

            def test_deliberate_write_agents_toml():
                target = Path({str(existing_file)!r})
                write_agents_toml(
                    target,
                    "[agents.claude]\\ncmd = 'claude'\\n[agents.opencode]\\ncmd = 'opencode'\\n",
                    trigger="test",
                )
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "ralph-workflow-agents.toml" in output


def test_extract_read_path_ignores_write_modes_and_non_guarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    target = guarded_dir / "ralph-workflow.toml"
    target.write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    assert _extract_read_path((str(target), "r")) == target.resolve()
    assert _extract_read_path((str(target), "rb")) == target.resolve()
    assert _extract_read_path((target, "r")) == target.resolve()
    assert _extract_read_path((os.fsencode(str(target)), "r")) == target.resolve()

    assert _extract_read_path((str(target), "w")) is None
    assert _extract_read_path((str(target), "a")) is None
    assert _extract_read_path((str(target), "x")) is None
    assert _extract_read_path((str(target), "r+")) is None
    assert _extract_read_path((str(target), "rb+", os.O_RDWR)) is None
    assert _extract_read_path((str(target), "rb", os.O_WRONLY)) is None

    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    assert _extract_read_path((str(tmp_path / "other.toml"), "r")) is None
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "ralph-workflow.toml"
    assert _extract_read_path((str(outside_file), "r")) is None


def test_extract_read_path_handles_dotfile_and_nested_prefixes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)

    dot_dir = guarded_dir / ".ralph-workflow"
    dot_dir.mkdir()
    dot_file = dot_dir / "state.json"
    dot_file.write_text("{}", encoding="utf-8")

    sub_dir = guarded_dir / "ralph-workflow"
    sub_dir.mkdir()
    nested_file = sub_dir / "config.toml"
    nested_file.write_text("key = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    assert _extract_read_path((str(dot_file), "r")) == dot_file.resolve()
    assert _extract_read_path((str(nested_file), "r")) == nested_file.resolve()


def test_bundled_defaults_agents_toml_is_protected_and_valid() -> None:
    from ralph.policy.loader import default_dir

    bundled_dir = default_dir()
    bundled_agents = bundled_dir / "ralph-workflow-agents.toml"
    assert bundled_agents.resolve() == _BUNDLED_AGENTS_TEMPLATE.resolve()
    assert bundled_agents.exists()
    assert bundled_agents.is_file()

    initial_bytes = bundled_agents.read_bytes()
    content = initial_bytes.decode("utf-8")
    parsed = tomllib.loads(content)
    assert isinstance(parsed["agents"], dict)
    assert parsed["agents"]["claude"]["cmd"] == "claude"
    assert "[agents.claude]" in content
    assert "# @AGENT-BLOCK-START: codex" in content
    assert bundled_agents.read_bytes() == initial_bytes


def test_default_guard_snapshot_includes_bundled_agents_template() -> None:
    snapshot = _scan_guarded_config_files()

    assert _BUNDLED_AGENTS_TEMPLATE.resolve() in snapshot
    guarded = snapshot[_BUNDLED_AGENTS_TEMPLATE.resolve()]
    assert guarded.sha256
    assert not guarded.is_dir


def test_scan_guarded_config_files_with_explicit_dirs_includes_bundled_template(
    tmp_path: Path,
) -> None:
    snapshot = _scan_guarded_config_files(guarded_dirs=[tmp_path])
    assert _BUNDLED_AGENTS_TEMPLATE.resolve() in snapshot
    guarded = snapshot[_BUNDLED_AGENTS_TEMPLATE.resolve()]
    assert guarded.sha256
    assert not guarded.is_dir


def test_explicit_dir_scan_detects_bundled_template_content_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_template_bytes = _BUNDLED_AGENTS_TEMPLATE.read_bytes()

    synthetic = tmp_path / "synthetic-ralph-workflow-agents.toml"
    original_content = "[agents.claude]\ncmd = 'claude'\n"
    mutated_content = "[agents.claude]\ncmd = 'Claude'\n"
    synthetic.write_text(original_content, encoding="utf-8")

    monkeypatch.setitem(
        _scan_guarded_config_files.__globals__,
        "_BUNDLED_AGENTS_TEMPLATE",
        synthetic,
    )

    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)

    before = _scan_guarded_config_files(guarded_dirs=[tmp_path])

    assert synthetic.resolve() in before
    before_snapshot = before[synthetic.resolve()]
    assert before_snapshot.sha256
    assert not before_snapshot.is_dir

    original_stat = synthetic.stat()
    synthetic.write_text(mutated_content, encoding="utf-8")
    os.utime(synthetic, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    after = _scan_guarded_config_files(guarded_dirs=[tmp_path])
    after_snapshot = after[synthetic.resolve()]

    assert before_snapshot.sha256 != after_snapshot.sha256
    assert before_snapshot.size == after_snapshot.size
    assert before_snapshot.mtime_ns == after_snapshot.mtime_ns

    violations = _diff_config_snapshots(before, after)
    synthetic_violations = [v for v in violations if str(synthetic.resolve()) in v]

    assert len(synthetic_violations) == 1
    assert synthetic_violations[0].startswith("MODIFIED (content hash changed): ")
    assert "content hash changed" in synthetic_violations[0]
    assert "mtime changed" not in synthetic_violations[0]

    assert _BUNDLED_AGENTS_TEMPLATE.read_bytes() == real_template_bytes


def test_real_config_guard_catches_symlink_unprefixed_and_transient_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    real_file = guarded_dir / "target.toml"
    real_file.write_text("key = 'secret'\n", encoding="utf-8")

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True)
    symlink_file = outside_dir / "symlink_to_target.toml"
    symlink_file.symlink_to(real_file)

    unprefixed_file = guarded_dir / "other_settings.json"
    unprefixed_file.write_text("{}", encoding="utf-8")

    transient_file = guarded_dir / "transient.toml"

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_cases=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_access_via_symlink():
                link = Path({str(symlink_file)!r})
                _ = link.read_text(encoding="utf-8")

            def test_access_unprefixed():
                target = Path({str(unprefixed_file)!r})
                _ = target.read_text(encoding="utf-8")

            def test_transient_write_and_delete():
                target = Path({str(transient_file)!r})
                target.write_text("transient = true\\n", encoding="utf-8")
                target.unlink()
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=3, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "READ:" in output
    assert "target.toml" in output
    assert "other_settings.json" in output
    assert "TRANSIENT MUTATION:" in output
    assert "transient.toml" in output


def test_default_production_guard_detects_unprefixed_real_config_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "fake_home"
    fake_config = fake_home / ".config"
    fake_config.mkdir(parents=True)
    unprefixed_file = fake_config / "unrelated-app.toml"
    unprefixed_file.write_text("setting = 1\n", encoding="utf-8")

    nested_dir = fake_config / "nested_app"
    nested_dir.mkdir()
    nested_unprefixed = nested_dir / "settings.json"
    nested_unprefixed.write_text("{}", encoding="utf-8")

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    symlink_file = outside_dir / "symlink_to_unprefixed.toml"
    symlink_file.symlink_to(unprefixed_file)

    symlink_dir = outside_dir / "config_alias"
    symlink_dir.symlink_to(fake_config)
    alias_child = symlink_dir / "unrelated-app.toml"

    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_HOME", fake_home)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_XDG_CONFIG_HOME", fake_config)

    assert _extract_read_path((str(unprefixed_file), "r")) == unprefixed_file.resolve()
    assert _extract_read_path((unprefixed_file, "rb")) == unprefixed_file.resolve()
    assert _extract_read_path((str(nested_unprefixed), "r")) == nested_unprefixed.resolve()

    assert _extract_write_path((str(unprefixed_file), "w")) == unprefixed_file.resolve()
    assert _extract_write_path((str(unprefixed_file), "a")) == unprefixed_file.resolve()
    assert _extract_write_path((str(unprefixed_file), "r+", os.O_RDWR)) == unprefixed_file.resolve()
    assert _extract_guarded_path("os.unlink", (str(unprefixed_file),)) == unprefixed_file.resolve()
    assert _extract_guarded_path("os.remove", (str(unprefixed_file),)) == unprefixed_file.resolve()
    assert _extract_guarded_path("os.rmdir", (str(nested_dir),)) == nested_dir.resolve()
    new_dir = fake_config / "new_app"
    assert _extract_guarded_path("os.mkdir", (str(new_dir),)) == new_dir.resolve()
    assert (
        _extract_guarded_path(
            "os.rename", (str(unprefixed_file), str(fake_config / "renamed.toml"))
        )
        == unprefixed_file.resolve()
    )

    assert _extract_read_path((str(symlink_file), "r")) == unprefixed_file.resolve()
    assert _extract_write_path((str(symlink_file), "w")) == unprefixed_file.resolve()
    assert _extract_read_path((str(alias_child), "r")) == unprefixed_file.resolve()
    assert _extract_write_path((str(alias_child), "w")) == unprefixed_file.resolve()


def test_default_production_guard_preserves_ralph_and_home_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "fake_home"
    fake_config = fake_home / ".config"
    fake_config.mkdir(parents=True)

    ralph_config_file = fake_config / "ralph-workflow.toml"
    ralph_config_file.write_text("k = 1\n", encoding="utf-8")
    ralph_sub_file = fake_config / "ralph-workflow" / "config.toml"
    ralph_sub_file.parent.mkdir()
    ralph_sub_file.write_text("k = 2\n", encoding="utf-8")

    home_ralph_file = fake_home / "ralph-workflow.toml"
    home_ralph_file.write_text("k = 3\n", encoding="utf-8")
    home_dot_ralph = fake_home / ".ralph-workflow" / "state.json"
    home_dot_ralph.parent.mkdir()
    home_dot_ralph.write_text("{}", encoding="utf-8")

    home_unrelated = fake_home / "unrelated.txt"
    home_unrelated.write_text("hello\n", encoding="utf-8")
    home_bashrc = fake_home / ".bashrc"
    home_bashrc.write_text("export FOO=1\n", encoding="utf-8")
    home_local = fake_home / ".local" / "share" / "data.txt"
    home_local.parent.mkdir(parents=True)
    home_local.write_text("data\n", encoding="utf-8")

    outside_file = tmp_path / "outside" / "other.toml"
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    outside_file.write_text("x = 1\n", encoding="utf-8")

    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_HOME", fake_home)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_XDG_CONFIG_HOME", fake_config)

    assert _extract_read_path((str(ralph_config_file), "r")) == ralph_config_file.resolve()
    assert _extract_read_path((str(ralph_sub_file), "r")) == ralph_sub_file.resolve()
    assert _extract_read_path((str(home_ralph_file), "r")) == home_ralph_file.resolve()
    assert _extract_read_path((str(home_dot_ralph), "r")) == home_dot_ralph.resolve()

    assert _extract_read_path((str(home_unrelated), "r")) is None
    assert _extract_write_path((str(home_unrelated), "w")) is None
    assert _extract_read_path((str(home_bashrc), "r")) is None
    assert _extract_write_path((str(home_bashrc), "w")) is None
    assert _extract_read_path((str(home_local), "r")) is None
    assert _extract_write_path((str(home_local), "w")) is None

    assert _extract_read_path((str(outside_file), "r")) is None
    assert _extract_write_path((str(outside_file), "w")) is None


def test_proc_self_root_alias_detection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Guarded path detection catches aliases resolving to protected config via /proc/self/root."""
    fake_home = tmp_path / "fake_home"
    fake_config = fake_home / ".config"
    fake_config.mkdir(parents=True)
    protected_file = fake_config / "ralph-workflow-agents.toml"
    protected_file.write_text("k = 1\n", encoding="utf-8")

    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_HOME", fake_home)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_XDG_CONFIG_HOME", fake_config)

    proc_root = Path("/proc/self/root")
    if not proc_root.exists():
        pytest.skip("/proc/self/root is not available on this platform")

    proc_alias = proc_root / str(protected_file).lstrip("/")
    assert _extract_read_path((str(proc_alias), "r")) == protected_file.resolve()


def test_default_production_session_guard_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "fake_home"
    fake_config = fake_home / ".config"
    fake_config.mkdir(parents=True)
    unprefixed_file = fake_config / "other_settings.json"
    unprefixed_file.write_text('{"unrelated": true}\n', encoding="utf-8")

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True)
    symlink_file = outside_dir / "symlink_to_other.json"
    symlink_file.symlink_to(unprefixed_file)

    transient_file = fake_config / "transient_unprefixed.toml"
    home_bashrc = fake_home / ".bashrc"
    home_bashrc.write_text("export TEST=1\n", encoding="utf-8")

    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_HOME", fake_home)
    monkeypatch.setattr("tests._real_config_guard_support.REAL_XDG_CONFIG_HOME", fake_config)

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_cases=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_unprefixed_read_detected():
                target = Path({str(unprefixed_file)!r})
                _ = target.read_text(encoding="utf-8")

            def test_symlink_access_detected():
                link = Path({str(symlink_file)!r})
                _ = link.read_text(encoding="utf-8")

            def test_transient_mutation_detected():
                target = Path({str(transient_file)!r})
                target.write_text("key = 'val'\\n", encoding="utf-8")
                target.unlink()

            def test_non_config_home_path_permitted():
                bashrc = Path({str(home_bashrc)!r})
                _ = bashrc.read_text(encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=4, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "READ:" in output
    assert "other_settings.json" in output
    assert "TRANSIENT MUTATION:" in output
    assert "transient_unprefixed.toml" in output
    assert ".bashrc" not in output
