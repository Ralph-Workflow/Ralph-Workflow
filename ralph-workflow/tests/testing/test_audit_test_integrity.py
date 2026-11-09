"""Tests for :mod:`ralph.testing.audit_test_integrity`.

The gate has two halves and every case below exercises one of them in both
polarities:

* the diff half flags a removed / changed test check that rides along with a
  production change, and accepts it when a ``# RALPH-TEST-REASON:`` reason is
  recorded next to the change;
* the detection half flags production code that reads the test runner, and
  accepts production code that does not.

Every input is synthetic: the diff is a literal string and the production
tree is written into ``tmp_path``. No network, no subprocess, no git.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.testing.audit_test_integrity import (
    CATEGORY_PYTEST_DETECTION,
    CATEGORY_TEST_WEAKENING,
    TEST_REASON_MARKER,
    TestIntegrityFinding,
    audit_pytest_detection,
    audit_test_integrity_diff,
    main,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_PRODUCTION_DIFF = """\
diff --git a/ralph/core.py b/ralph/core.py
index 1111111..2222222 100644
--- a/ralph/core.py
+++ b/ralph/core.py
@@ -1,3 +1,3 @@
 def run():
-    return 1
+    return 2
"""

_TEST_FILE_HEADER = """\
diff --git a/tests/test_core.py b/tests/test_core.py
index 3333333..4444444 100644
--- a/tests/test_core.py
+++ b/tests/test_core.py
"""


def _diff(*sections: str) -> str:
    """Return a unified diff built from *sections*."""
    return "\n".join(sections)


def _test_hunk(*lines: str) -> str:
    """Return a test-file hunk carrying *lines* (already prefixed)."""
    body = "\n".join(lines)
    return f"{_TEST_FILE_HEADER}@@ -10,4 +10,4 @@\n{body}\n"


def _categories(findings: Sequence[TestIntegrityFinding]) -> set[str]:
    """Return the categories of *findings*."""
    return {finding.category for finding in findings}


def _write(root: Path, relative: str, source: str) -> Path:
    """Write *source* to *relative* under *root* and return the path."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


# --- the diff half: a removed check riding along with production code ------


def test_removed_assertion_with_a_production_change_is_reported() -> None:
    """Deleting the assertion that pins the production behaviour is a finding."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result == 1", "+    assert result == 2"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    finding = findings[0]
    assert finding.file_path == "tests/test_core.py"
    assert finding.line == 11
    assert "assert result == 1" in finding.detail
    assert "ralph/core.py" in finding.detail
    assert "fix:" in finding.detail


def test_recorded_reason_on_an_added_line_suppresses_the_finding() -> None:
    """A recorded reason next to the change is the documented escape hatch."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            "-    assert result == 1",
            f"+    # {TEST_REASON_MARKER} the contract changed from 1 to 2",
            "+    assert result == 2",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_recorded_reason_on_the_removed_line_suppresses_the_finding() -> None:
    """The marker may also sit on the removed line itself."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            f"-    assert result == 1  # {TEST_REASON_MARKER} contract changed",
            "+    assert result == 2",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_changed_expected_value_is_reported() -> None:
    """Editing the expected value to match the new behaviour is a finding."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            "-    assert result == 1",
            "+    assert result == 2",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    assert "assert result == 1" in findings[0].detail


def test_removed_fixture_is_reported() -> None:
    """Dropping a fixture removes the setup the test depended on."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("-@pytest.fixture", " def sample():", "     return 1"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    assert "pytest fixture" in findings[0].detail


def test_changed_expected_value_constant_is_reported() -> None:
    """A changed EXPECTED constant is a changed expected value."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("-EXPECTED_RESULT = 1", "+EXPECTED_RESULT = 2"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    assert "expected-value constant" in findings[0].detail


def test_a_test_only_change_is_not_reported() -> None:
    """Without a production change in the same diff there is no coupling."""
    diff = _diff(_test_hunk("     result = run()", "-    assert result == 1"))

    assert audit_test_integrity_diff(diff) == []


def test_an_added_test_is_not_reported() -> None:
    """Adding a check is the opposite of weakening one."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "+    assert result == 2"),
    )

    assert audit_test_integrity_diff(diff) == []


def test_a_strengthened_assertion_is_not_reported() -> None:
    """A bare assertion replaced by a specific one adds a constraint."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result", "+    assert result == 2"),
    )

    assert audit_test_integrity_diff(diff) == []


def test_a_moved_assertion_is_not_reported() -> None:
    """The same assertion on another line is a move, not a weakening."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("-    assert result == 1", "+    assert result == 1"),
    )

    assert audit_test_integrity_diff(diff) == []


def test_a_weakened_assertion_is_reported() -> None:
    """Dropping the comparison keeps a weaker check in place."""
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result == 1", "+    assert result"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


# --- the detection half: production code must not sniff the test runner ----


def test_production_code_reading_the_pytest_env_variable_is_reported(tmp_path: Path) -> None:
    """Reading the pytest current-test variable changes shipped behaviour."""
    _write(
        tmp_path,
        "ralph/probe.py",
        "import os\n"
        "\n"
        "\n"
        "def in_test() -> bool:\n"
        '    return os.environ.get("PYTEST_CURRENT_TEST") is not None\n',
    )

    findings = audit_pytest_detection(tmp_path)

    assert _categories(findings) == {CATEGORY_PYTEST_DETECTION}
    assert findings[0].file_path == "ralph/probe.py"
    assert "fix:" in findings[0].detail


def test_production_code_probing_sys_modules_is_reported(tmp_path: Path) -> None:
    """Looking the pytest package up in sys.modules is the same probe."""
    _write(
        tmp_path,
        "ralph/probe.py",
        'import sys\n\n\ndef in_test() -> bool:\n    return "pytest" in sys.modules\n',
    )

    findings = audit_pytest_detection(tmp_path)

    assert _categories(findings) == {CATEGORY_PYTEST_DETECTION}


def test_production_code_subscripting_sys_modules_is_reported(tmp_path: Path) -> None:
    """The subscript form of the probe is detected too."""
    _write(
        tmp_path,
        "ralph/probe.py",
        'import sys\n\n\ndef in_test() -> bool:\n    return sys.modules["pytest"] is not None\n',
    )

    findings = audit_pytest_detection(tmp_path)

    assert _categories(findings) == {CATEGORY_PYTEST_DETECTION}


def test_production_code_that_does_not_probe_is_clean(tmp_path: Path) -> None:
    """Ordinary production code produces no finding."""
    _write(
        tmp_path,
        "ralph/core.py",
        "def run() -> int:\n    return 1\n",
    )

    assert audit_pytest_detection(tmp_path) == []


def test_prose_naming_the_pytest_variable_is_not_a_finding(tmp_path: Path) -> None:
    """The detector is AST-based: a docstring that names the variable is fine."""
    _write(
        tmp_path,
        "ralph/core.py",
        '"""A module whose docstring names PYTEST_CURRENT_TEST in prose."""\n'
        "\n"
        "\n"
        "def run() -> int:\n    return 1\n",
    )

    assert audit_pytest_detection(tmp_path) == []


def test_test_files_are_out_of_scope_for_the_detection(tmp_path: Path) -> None:
    """Only production code is scanned for test-runner detection."""
    _write(
        tmp_path,
        "tests/test_probe.py",
        "import os\n"
        "\n"
        "\n"
        "def test_probe() -> None:\n"
        '    assert os.environ.get("PYTEST_CURRENT_TEST") is not None\n',
    )
    _write(tmp_path, "ralph/core.py", "def run() -> int:\n    return 1\n")

    assert audit_pytest_detection(tmp_path) == []


# --- the CLI contract -------------------------------------------------------


def _cli_root(tmp_path: Path) -> Path:
    """Return a repository root with an empty production package."""
    _write(tmp_path, "ralph/core.py", "def run() -> int:\n    return 1\n")
    return tmp_path


def test_main_returns_one_when_the_diff_weakens_a_test(tmp_path: Path) -> None:
    """A weakening diff fails the CLI."""
    root = _cli_root(tmp_path)
    diff_path = root / "change.diff"
    diff_path.write_text(
        _diff(
            _PRODUCTION_DIFF,
            _test_hunk("     result = run()", "-    assert result == 1"),
        ),
        encoding="utf-8",
    )

    assert main(["--diff", str(diff_path), "--root", str(root)]) == 1


def test_main_returns_zero_when_the_diff_is_clean(tmp_path: Path) -> None:
    """A diff with a recorded reason passes the CLI."""
    root = _cli_root(tmp_path)
    diff_path = root / "change.diff"
    diff_path.write_text(
        _diff(
            _PRODUCTION_DIFF,
            _test_hunk(
                "     result = run()",
                "-    assert result == 1",
                f"+    # {TEST_REASON_MARKER} the contract changed",
            ),
        ),
        encoding="utf-8",
    )

    assert main(["--diff", str(diff_path), "--root", str(root)]) == 0


def test_main_returns_two_on_an_unknown_argument(tmp_path: Path) -> None:
    """A usage error is reported as an error, not a silent pass."""
    assert main(["--nope", str(tmp_path)]) == 2


def test_main_returns_two_on_a_missing_root(tmp_path: Path) -> None:
    """A missing root is an error, not a silent pass."""
    assert main(["--root", str(tmp_path / "absent")]) == 2


def test_main_returns_two_when_the_root_has_no_production_package(tmp_path: Path) -> None:
    """A root without the production package is an error, not a silent pass."""
    assert main(["--root", str(tmp_path)]) == 2


def test_the_gate_never_shells_out_or_touches_the_network() -> None:
    """The gate reads files and parses a diff; it never spawns a process."""
    module_path = (
        Path(__file__).resolve().parents[2] / "ralph" / "testing" / "audit_test_integrity.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert "import subprocess" not in source
    assert "os.system" not in source
    assert "urllib" not in source
    assert "httpx" not in source
    assert "requests" not in source


def test_main_returns_two_on_an_unreadable_diff(tmp_path: Path) -> None:
    """An unreadable diff is an error, not a silent pass."""
    root = _cli_root(tmp_path)

    assert main(["--diff", str(root / "absent.diff"), "--root", str(root)]) == 2


def test_main_prints_the_findings_with_file_line_and_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The finding output names the file, the line and the fix."""
    root = _cli_root(tmp_path)
    diff_path = root / "change.diff"
    diff_path.write_text(
        _diff(
            _PRODUCTION_DIFF,
            _test_hunk("     result = run()", "-    assert result == 1"),
        ),
        encoding="utf-8",
    )

    main(["--diff", str(diff_path), "--root", str(root)])

    output = capsys.readouterr().out
    assert "TEST-INTEGRITY FINDINGS: 1 finding(s)" in output
    assert "tests/test_core.py:11: [test-weakening]" in output
    assert "fix:" in output
