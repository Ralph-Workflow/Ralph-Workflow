"""Tests for ralph.testing.audit_test_policy."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import TYPE_CHECKING

import pytest

from ralph.testing.audit_test_policy import (
    TestPolicyViolation,
    audit_test_file,
    audit_tests_directory,
    count_private_attrs,
    count_private_imports,
    count_setattr_private,
    main,
    render_whitebox_baseline,
)
from ralph.testing.test_whitebox_baseline import WHITEBOX_BASELINE

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


def test_open_with_exact_monkeypatch_is_allowed(tmp_path: Path) -> None:
    """open() is allowed when builtins.open itself is patched."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("monkeypatch.setattr('builtins.open', fake_open)\nopen('file.txt')\n")
    violations = audit_test_file(test_file)
    assert violations == []


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


def test_io_allowlist_skips_only_io_and_keeps_whitebox_check(tmp_path: Path) -> None:
    """An I/O-allowlisted file still receives the white-box ratchet check."""
    test_file = tmp_path / "test_multimodal_session_memory_regression.py"
    test_file.write_text("from ralph.example import _private\nopen('file.txt')\n")
    violations = audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["whitebox-baseline-missing"]


def test_wall_clock_allowlist_file_is_skipped(tmp_path: Path) -> None:
    """File with stem in _WALL_CLOCK_ALLOWLIST should be skipped entirely."""
    test_file = tmp_path / "test_timeout_clock.py"
    test_file.write_text("import time\ntime.monotonic()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_source_read_allowlist_permits_reading_shipped_files(tmp_path: Path) -> None:
    """A source-read allowlisted file may read repository files."""
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text("from pathlib import Path\nPath('policy.md').read_text()\n")
    assert audit_test_file(test_file) == []


def test_source_read_allowlist_still_flags_wall_clock(tmp_path: Path) -> None:
    """The narrow exemption must not suppress wall-clock violations."""
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text("import time\ntime.monotonic()\n")
    violations = audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["wall-clock"]


def test_source_read_allowlist_still_flags_subprocess(tmp_path: Path) -> None:
    """The narrow exemption must not suppress subprocess violations."""
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text("import subprocess\nsubprocess.run(['ls'])\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert "subprocess.run" in violations[0].detail


def test_source_read_allowlist_still_flags_sleep_and_writes(tmp_path: Path) -> None:
    """The exemption covers reads only: sleep and filesystem writes stay flagged."""
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text(
        "import time\nfrom pathlib import Path\ntime.sleep(1)\nPath('policy.md').write_text('x')\n"
    )
    violations = audit_test_file(test_file)
    assert sorted(violation.category for violation in violations) == ["io", "sleep"]


# ---------------------------------------------------------------------------
# White-box detector and ratchet tests
# ---------------------------------------------------------------------------


def test_const_first_private_setattr_is_flagged() -> None:
    """A const-first monkeypatch target reaching a private name is counted."""
    tree = ast.parse('monkeypatch.setattr("ralph.example._secret", value)\n')
    assert count_setattr_private(tree) == 1


def test_name_first_private_setattr_is_flagged() -> None:
    """A name-first monkeypatch target reaching a private name is counted."""
    tree = ast.parse('monkeypatch.setattr(run_loop, "_save_checkpoint", fake)\n')
    assert count_setattr_private(tree) == 1


def test_public_setattr_target_is_not_flagged() -> None:
    """A public monkeypatch target is not white-box coupling."""
    tree = ast.parse('monkeypatch.setattr("ralph.example.public_name", value)\n')
    assert count_setattr_private(tree) == 0


def test_dunder_setattr_target_is_not_flagged() -> None:
    """Dunder monkeypatch targets are public and are not counted."""
    tree = ast.parse('monkeypatch.setattr("ralph.example.__wrapped__", value)\n')
    assert count_setattr_private(tree) == 0


def test_private_ralph_symbol_import_is_flagged_but_public_import_is_not() -> None:
    """Only imports reaching private production symbols are counted."""
    private_tree = ast.parse("from ralph.example import _private\n")
    public_tree = ast.parse("from ralph.example import public_name\n")
    assert count_private_imports(private_tree) == 1
    assert count_private_imports(public_tree) == 0


def test_dunder_private_import_is_not_flagged() -> None:
    """Dunder imports are public and are not counted as private coupling."""
    tree = ast.parse("from ralph.example import __doc__\n")
    assert count_private_imports(tree) == 0


def test_public_symbol_through_private_module_is_flagged() -> None:
    """A public symbol imported through a private ralph module is coupling."""
    tree = ast.parse("from ralph.example._private_module import public_name\n")
    assert count_private_imports(tree) == 1


def test_private_attribute_read_inside_assert_is_flagged() -> None:
    """A private attribute read in an assertion is high-confidence coupling."""
    tree = ast.parse("def test_value(owner):\n    assert owner._secret == 1\n")
    assert count_private_attrs(tree) == 1


def test_private_attribute_read_outside_assert_is_not_flagged() -> None:
    """An unrelated private attribute read outside the narrowed contexts is ignored."""
    tree = ast.parse("value = owner._secret\n")
    assert count_private_attrs(tree) == 0


def test_unrelated_monkeypatch_does_not_exempt_sleep(tmp_path: Path) -> None:
    """Mentioning monkeypatch does not suppress a real sleep call."""
    test_file = tmp_path / "test_sleep_policy.py"
    test_file.write_text(
        "monkeypatch.setattr('ralph.example.public_name', fake)\ntime.sleep(0.3)\n"
    )
    violations = audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["sleep"]


def test_exact_sleep_target_patch_exempts_sleep(tmp_path: Path) -> None:
    """Patching the exact sleep target exempts that sleep call."""
    test_file = tmp_path / "test_sleep_policy.py"
    test_file.write_text("monkeypatch.setattr('time.sleep', fake)\ntime.sleep(0.3)\n")
    violations = audit_test_file(test_file)
    assert violations == []


def test_whitebox_count_above_baseline_exits_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A count above its frozen ceiling is a fail-closed regression."""
    test_file = tmp_path / "test_whitebox_regression.py"
    test_file.write_text("from ralph.example import _private\n")
    baseline: Mapping[str, Mapping[str, int]] = {
        "tests/test_whitebox_regression.py": {
            "setattr_private": 0,
            "private_import": 0,
            "private_attr": 0,
        }
    }
    exit_code = main([str(tmp_path)], baseline=baseline)
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "whitebox-regression" in output


def test_nonzero_whitebox_count_missing_from_baseline_exits_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A new file with white-box usage fails closed when absent from baseline."""
    test_file = tmp_path / "test_whitebox_missing.py"
    test_file.write_text("from ralph.example import _private\n")
    exit_code = main([str(tmp_path)], baseline={})
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "whitebox-baseline-missing" in output


def test_emit_baseline_entry_set_matches_frozen_baseline() -> None:
    """The emitted inventory parses and contains the frozen baseline's file set."""
    emitted_text = render_whitebox_baseline(WHITEBOX_BASELINE)
    parsed = ast.parse(emitted_text)
    raw_assignment = next(
        node
        for node in parsed.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_RAW"
    )
    raw = ast.literal_eval(raw_assignment.value)
    emitted_paths = {line.split("|", 1)[0] for line in raw.splitlines() if line}
    assert emitted_paths == set(WHITEBOX_BASELINE)


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
