"""Meta-tests verifying real-config guard enforcement across the test suite."""

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
    _extract_read_path,
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
    """Default guarded directories must include real user config and home paths."""
    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    dirs = _resolve_guarded_config_dirs()
    assert len(dirs) >= 1
    dir_strings = [str(d) for d in dirs]
    assert any(".config" in s for s in dir_strings)


def test_resolve_guarded_config_dirs_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RALPH_TEST_GUARDED_CONFIG_DIRS overrides monitored directories."""
    custom_dir = tmp_path / "custom_config"
    custom_dir.mkdir()
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(custom_dir))
    dirs = _resolve_guarded_config_dirs()
    assert dirs == [custom_dir.resolve()]


def test_diff_config_snapshots_detection(tmp_path: Path) -> None:
    """Snapshot diffing detects file creation, modification, and deletion."""
    file_a = tmp_path / "ralph-workflow-a.toml"
    file_b = tmp_path / "ralph-workflow-b.toml"

    snap_a1 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1000, size=10, sha256="hash1")
    snap_b = _ConfigPathSnapshot(file_b, is_dir=False, mtime_ns=2000, size=20, sha256="hash2")

    # Identical snapshots: no violations
    assert _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a1}) == []

    # Created file
    diff_created = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a1, file_b: snap_b})
    assert len(diff_created) == 1
    assert f"CREATED: {file_b}" in diff_created[0]

    # Deleted file
    diff_deleted = _diff_config_snapshots({file_a: snap_a1, file_b: snap_b}, {file_a: snap_a1})
    assert len(diff_deleted) == 1
    assert f"DELETED: {file_b}" in diff_deleted[0]

    # Modified content hash
    snap_a2 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1001, size=12, sha256="hash_mod")
    diff_hash = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a2})
    assert len(diff_hash) == 1
    assert f"MODIFIED (content hash changed): {file_a}" in diff_hash[0]

    # Modified mtime only
    snap_a3 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1002, size=10, sha256="hash1")
    diff_mtime = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a3})
    assert len(diff_mtime) == 1
    assert f"MODIFIED (mtime changed): {file_a}" in diff_mtime[0]

    # Modified mode only
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
    """Guarded scanner captures files and nested directories matching ralph-workflow*."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    top_file = config_dir / "ralph-workflow.toml"
    top_file.write_text("key = 1\n", encoding="utf-8")

    sub_dir = config_dir / "ralph-workflow-data"
    sub_dir.mkdir()
    nested_file = sub_dir / "state.json"
    nested_file.write_text("{}", encoding="utf-8")

    # Unrelated file that does not match the pattern
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
    """A test that creates a file under the guarded config path fails loudly."""
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
    """A test that modifies a file under the guarded config path fails loudly."""
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
    """A test that deletes a file under the guarded config path fails loudly."""
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
    """A test that chmods a file under the guarded config path fails loudly."""
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
    """A test that reads a file under the guarded config path fails loudly."""
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
    """A clean test suite that does not touch guarded files passes without errors."""
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
    """The autouse test environment isolates HOME, XDG_CONFIG_HOME, and config paths."""
    # Under the autouse isolation fixture:
    # 1. Path.home() is sandboxed
    home = Path.home()
    assert "autouse-home" in str(home)

    # 2. GLOBAL_CONFIG_PATH is redirected to the sandboxed path
    assert "autouse-home" in str(config_loader.GLOBAL_CONFIG_PATH)
    assert config_loader.GLOBAL_CONFIG_PATH.name == "ralph-workflow.toml"

    # 3. GLOBAL_AGENTS_CONFIG_PATH is redirected to the sandboxed path
    assert "autouse-home" in str(config_loader.GLOBAL_AGENTS_CONFIG_PATH)
    assert config_loader.GLOBAL_AGENTS_CONFIG_PATH.name == "ralph-workflow-agents.toml"

    # 4. DEFAULT_STATE_PATH is redirected to the sandboxed path
    assert "autouse-home" in str(state_store.DEFAULT_STATE_PATH)
    assert state_store.DEFAULT_STATE_PATH.name == "ralph-workflow-capabilities.json"


def test_autouse_isolation_registers_current_test_node(request: pytest.FixtureRequest) -> None:
    """The autouse isolation fixture sets _STATE.current_test_node to request.node.nodeid."""
    assert _STATE.current_test_node == request.node.nodeid


def test_read_violations_tracking_helpers(tmp_path: Path) -> None:
    """_get_test_read_violations filters by node and _read_violations_count tracks total."""
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

    # Clean up test-injected reads so session guard stays clean
    _clear_recorded_read_violations()


def test_real_config_guard_attributes_read_violation_to_node_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that reads a guarded file is explicitly named in the violation message."""
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
    """write_agents_toml targeting a guarded config directory is caught by the guard."""
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
    """_extract_read_path ignores write-mode opens and paths outside guarded dirs."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    target = guarded_dir / "ralph-workflow.toml"
    target.write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    # Read modes are identified as guarded reads
    assert _extract_read_path((str(target), "r")) == target.resolve()
    assert _extract_read_path((str(target), "rb")) == target.resolve()
    assert _extract_read_path((target, "r")) == target.resolve()
    assert _extract_read_path((os.fsencode(str(target)), "r")) == target.resolve()

    # Write / append / update modes are ignored (handled by snapshot diffing)
    assert _extract_read_path((str(target), "w")) is None
    assert _extract_read_path((str(target), "a")) is None
    assert _extract_read_path((str(target), "x")) is None
    assert _extract_read_path((str(target), "r+")) is None
    assert _extract_read_path((str(target), "rb+", os.O_RDWR)) is None
    assert _extract_read_path((str(target), "rb", os.O_WRONLY)) is None

    # Paths without 'ralph-workflow' in name are ignored
    other_file = guarded_dir / "other.toml"
    assert _extract_read_path((str(other_file), "r")) is None

    # Files outside guarded directories are ignored even if named ralph-workflow
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "ralph-workflow.toml"
    assert _extract_read_path((str(outside_file), "r")) is None


def test_extract_read_path_handles_dotfile_and_nested_prefixes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_extract_read_path catches reads in .ralph-workflow and nested ralph-workflow directories."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)

    # Dotfile directory .ralph-workflow
    dot_dir = guarded_dir / ".ralph-workflow"
    dot_dir.mkdir()
    dot_file = dot_dir / "state.json"
    dot_file.write_text("{}", encoding="utf-8")

    # Nested directory ralph-workflow
    sub_dir = guarded_dir / "ralph-workflow"
    sub_dir.mkdir()
    nested_file = sub_dir / "config.toml"
    nested_file.write_text("key = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    assert _extract_read_path((str(dot_file), "r")) == dot_file.resolve()
    assert _extract_read_path((str(nested_file), "r")) == nested_file.resolve()


def test_bundled_defaults_agents_toml_is_protected_and_valid() -> None:
    """Bundled ralph-workflow-agents.toml defaults template exists and is valid TOML."""
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
    """The session guard snapshots the bundled agents template against mutation."""
    snapshot = _scan_guarded_config_files()

    assert _BUNDLED_AGENTS_TEMPLATE.resolve() in snapshot
    guarded = snapshot[_BUNDLED_AGENTS_TEMPLATE.resolve()]
    assert guarded.sha256
    assert not guarded.is_dir
