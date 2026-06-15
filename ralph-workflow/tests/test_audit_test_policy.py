"""Tests for ralph.testing.audit_test_policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_test_policy import (
    TestPolicyViolation,
    audit_test_file,
    audit_tests_directory,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Core function tests — audit_test_file
# ---------------------------------------------------------------------------


def test_sleep_with_positive_arg_is_violation(tmp_path: Path) -> None:
    """time.sleep(1) should be detected as a sleep violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import time\ntime.sleep(1)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "sleep"


def test_sleep_with_zero_arg_is_allowed(tmp_path: Path) -> None:
    """time.sleep(0) should NOT be flagged."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import time\ntime.sleep(0)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_asyncio_sleep_zero_is_allowed(tmp_path: Path) -> None:
    """asyncio.sleep(0) should NOT be flagged."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import asyncio\nasyncio.sleep(0)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_asyncio_sleep_positive_is_violation(tmp_path: Path) -> None:
    """asyncio.sleep(0.5) should be detected as a sleep violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import asyncio\nasyncio.sleep(0.5)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "sleep"


def test_open_is_violation(tmp_path: Path) -> None:
    """open() call should be detected as an I/O violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text('open("file.txt")\n')
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_open_with_monkeypatch_is_allowed(tmp_path: Path) -> None:
    """open() in a file containing monkeypatch.setattr should be allowed."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("monkeypatch.setattr(target, 'open', fake_open)\nopen('file.txt')\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_path_read_text_is_violation(tmp_path: Path) -> None:
    """Path('f.txt').read_text() should be detected as I/O violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("from pathlib import Path\nPath('f.txt').read_text()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_path_read_text_with_tmp_path_is_allowed(tmp_path: Path) -> None:
    """Path().read_text() with tmp_path in source should be allowed."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("from pathlib import Path\np = tmp_path / 'f.txt'\nPath(p).read_text()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_subprocess_run_is_violation(tmp_path: Path) -> None:
    """subprocess.run() should be detected as an I/O violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import subprocess\nsubprocess.run(['cmd'])\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_subprocess_with_e2e_marker_is_allowed(tmp_path: Path) -> None:
    """File with @pytest.mark.subprocess_e2e AND subprocess.run() should be allowed."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        "import pytest\nimport subprocess\n\n"
        "@pytest.mark.subprocess_e2e\n"
        "def test_thing():\n"
        "    subprocess.run(['cmd'])\n"
    )
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_socket_creation_is_violation(tmp_path: Path) -> None:
    """socket.socket() should be detected as an I/O violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import socket\nsocket.socket()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_time_monotonic_is_violation(tmp_path: Path) -> None:
    """time.monotonic() should be detected as a wall-clock violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import time\ntime.monotonic()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "wall-clock"


def test_blocking_wait_is_violation(tmp_path: Path) -> None:
    """event.wait() without timeout should be detected as blocking-wait violation."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import threading\nevent = threading.Event()\nevent.wait()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "blocking-wait"


def test_wait_with_timeout_is_allowed(tmp_path: Path) -> None:
    """event.wait(timeout=5) with timeout should be allowed."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import threading\nevent = threading.Event()\nevent.wait(timeout=5)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_clean_file_no_violations(tmp_path: Path) -> None:
    """A test file with no policy violations should return empty list."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("def test_hello():\n    assert True\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_non_py_file_returns_empty(tmp_path: Path) -> None:
    """A non-.py file should return empty violations list."""
    test_file = tmp_path / "test_example.txt"
    test_file.write_text("time.sleep(1)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# Allowlist tests
# ---------------------------------------------------------------------------


def test_io_allowlist_file_is_skipped(tmp_path: Path) -> None:
    """File with stem in _IO_ALLOWLIST should be skipped entirely."""
    test_file = tmp_path / "test_multimodal_session_memory_regression.py"
    test_file.write_text("open('file.txt')\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_wall_clock_allowlist_file_is_skipped(tmp_path: Path) -> None:
    """File with stem in _WALL_CLOCK_ALLOWLIST should be skipped entirely."""
    test_file = tmp_path / "test_timeout_clock.py"
    test_file.write_text("import time\ntime.monotonic()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# Directory-level audit test
# ---------------------------------------------------------------------------


def test_audit_tests_directory(tmp_path: Path) -> None:
    """Create a tmp directory with clean files and violation files, verify correct count."""
    # Clean file
    (tmp_path / "test_clean.py").write_text("def test_ok():\n    assert True\n")
    # Violation file
    (tmp_path / "test_bad.py").write_text("import time\ntime.sleep(2)\n")
    # File in __pycache__ should be skipped
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "test_cached.py").write_text("import time\ntime.sleep(99)\n")

    violations, files_checked = audit_tests_directory(tmp_path)
    assert len(violations) == 1
    assert violations[0].category == "sleep"
    # files_checked should be 2 (clean + bad); __pycache__ is skipped
    assert files_checked >= 2


# ---------------------------------------------------------------------------
# Integration with main()
# ---------------------------------------------------------------------------


def test_audit_test_policy_main_exit_zero(tmp_path: Path) -> None:
    """main() on a clean test directory should return exit code 0."""
    (tmp_path / "test_a.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "test_b.py").write_text("def test_also_ok():\n    assert 1 + 1 == 2\n")

    result = main([str(tmp_path)])
    assert result == 0


def test_audit_test_policy_main_exit_one(tmp_path: Path) -> None:
    """main() on a directory with violations should return exit code 1."""
    (tmp_path / "test_bad.py").write_text("import time\ntime.sleep(3)\n")

    result = main([str(tmp_path)])
    assert result == 1


def test_audit_test_policy_main_missing_dir() -> None:
    """main() with nonexistent directory should return exit code 2."""
    result = main(["/nonexistent/path/98765"])
    assert result == 2


# ---------------------------------------------------------------------------
# TestPolicyViolation __str__ test
# ---------------------------------------------------------------------------


def test_policy_violation_str_representation() -> None:
    """TestPolicyViolation.__str__ should contain file path, line, category, and detail."""
    v = TestPolicyViolation(
        file_path="tests/test_bad.py",
        line=42,
        category="sleep",
        detail="time.sleep(1) is bad",
    )
    s = str(v)
    assert "tests/test_bad.py" in s
    assert "42" in s
    assert "sleep" in s
    assert "time.sleep(1) is bad" in s


# ---------------------------------------------------------------------------
# Step 7: step-type-alias audit rule
# ---------------------------------------------------------------------------


def test_step_type_alias_value_raises_violation(tmp_path: Path) -> None:
    """step_type='test'/'tests'/'check'/'run' value raises step-type-alias violation."""
    fixture = tmp_path / "test_alias_user.py"
    fixture.write_text('def test_user():\n    s = {"step_type": "test"}\n')
    violations = audit_test_file(fixture)
    step_type_violations = [v for v in violations if v.category == "step-type-alias"]
    assert len(step_type_violations) >= 1
    assert "test" in step_type_violations[0].detail

    fixture_canonical = tmp_path / "test_canonical.py"
    fixture_canonical.write_text('def test_user():\n    s = {"step_type": "verify"}\n')
    canonical_violations = audit_test_file(fixture_canonical)
    canonical_step_type_violations = [
        v for v in canonical_violations if v.category == "step-type-alias"
    ]
    assert canonical_step_type_violations == []

    fixture_file_change = tmp_path / "test_file_change.py"
    fixture_file_change.write_text('def test_user():\n    s = {"step_type": "file_change"}\n')
    fc_violations = audit_test_file(fixture_file_change)
    fc_step_type_violations = [v for v in fc_violations if v.category == "step-type-alias"]
    assert fc_step_type_violations == []
