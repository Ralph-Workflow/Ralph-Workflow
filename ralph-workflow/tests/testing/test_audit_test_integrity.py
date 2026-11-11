from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.testing.audit_test_integrity import (
    CATEGORY_PYTEST_DETECTION,
    CATEGORY_TEST_WEAKENING,
    TEST_REASON_CONSTANT,
    TestIntegrityFinding,
    audit_pytest_detection,
    audit_test_integrity_diff,
    main,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "recorded reason contract": (
        "the recorded reason moved from an inline comment marker to a module-level constant entry"
    ),
}

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
    return "\n".join(sections)


def _test_hunk(*lines: str) -> str:
    body = "\n".join(lines)
    return f"{_TEST_FILE_HEADER}@@ -10,4 +10,4 @@\n{body}\n"


def _categories(findings: Sequence[TestIntegrityFinding]) -> set[str]:
    return {finding.category for finding in findings}


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_removed_assertion_with_a_production_change_is_reported() -> None:
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


def test_recorded_reason_in_the_added_constant_suppresses_the_finding() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            f"+{TEST_REASON_CONSTANT}: dict[str, str] = {{",
            '+    "test_core": "the contract changed from 1 to 2",',
            "+}",
            "-    assert result == 1",
            "+    assert result == 2",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_recorded_reason_only_on_a_removed_line_does_not_suppress_the_finding() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            f"-{TEST_REASON_CONSTANT}: dict[str, str] = {{",
            '-    "test_core": "the contract changed",',
            "-}",
            "-    assert result == 1",
            "+    assert result == 2",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


def test_changed_expected_value_is_reported() -> None:
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
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("-@pytest.fixture", " def sample():", "     return 1"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    assert "pytest fixture" in findings[0].detail


def test_changed_expected_value_constant_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("-EXPECTED_RESULT = 1", "+EXPECTED_RESULT = 2"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    assert "expected-value constant" in findings[0].detail


def test_a_test_only_change_is_not_reported() -> None:
    diff = _diff(_test_hunk("     result = run()", "-    assert result == 1"))

    assert audit_test_integrity_diff(diff) == []


def test_an_added_test_is_not_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "+    assert result == 2"),
    )

    assert audit_test_integrity_diff(diff) == []


def test_a_strengthened_assertion_is_not_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result", "+    assert result == 2"),
    )

    assert audit_test_integrity_diff(diff) == []


def test_a_moved_assertion_is_not_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("-    assert result == 1", "+    assert result == 1"),
    )

    assert audit_test_integrity_diff(diff) == []


def test_a_weakened_assertion_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result == 1", "+    assert result"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


def test_tautological_replacement_assert_result_to_one_equals_one_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result", "+    assert 1 == 1"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    assert "assert result" in findings[0].detail


def test_tautological_replacement_assert_is_not_none_to_true_equals_true_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()", "-    assert result is not None", "+    assert True == True"
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    assert "assert result is not None" in findings[0].detail


def test_tautological_replacement_assert_true_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result", "+    assert True"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


def test_tautological_replacement_reflexive_comparison_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()", "-    assert result == 1", "+    assert result == result"
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


def test_replacement_with_unrelated_expression_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result", "+    assert other == 2"),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


def test_empty_recorded_reason_does_not_suppress_finding() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            f"+{TEST_REASON_CONSTANT}: dict[str, str] = {{",
            '+    "test_core": "",',
            "+}",
            "-    assert result == 1",
            "+    assert result == 2",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


def test_whitespace_only_recorded_reason_does_not_suppress_finding() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            f"+{TEST_REASON_CONSTANT}: dict[str, str] = {{",
            '+    "test_core": "   ",',
            "+}",
            "-    assert result == 1",
            "+    assert result == 2",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}


def test_strengthened_assertion_with_is_not_none_is_not_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk("     result = run()", "-    assert result", "+    assert result is not None"),
    )

    assert audit_test_integrity_diff(diff) == []


def test_multiline_assertion_changed_expected_value_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            "     assert (",
            "-        result == 1",
            "+        result == 2",
            "     )",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    finding = findings[0]
    assert finding.file_path == "tests/test_core.py"
    assert finding.line == 12
    assert "assert (" in finding.detail
    assert "ralph/core.py" in finding.detail
    assert "fix:" in finding.detail


def test_multiline_assertion_recorded_reason_suppresses_finding() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            "     assert (",
            f"+{TEST_REASON_CONSTANT}: dict[str, str] = {{",
            '+    "test_core": "updated contract for new core behavior",',
            "+}",
            "-        result == 1",
            "+        result == 2",
            "     )",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_multiline_assertion_backslash_changed_expected_value_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            "     assert result \\",
            "-        == 1",
            "+        == 2",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    finding = findings[0]
    assert finding.file_path == "tests/test_core.py"
    assert finding.line == 12
    assert "ralph/core.py" in finding.detail


def test_multiline_assertion_reformatted_same_value_is_not_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            "     result = run()",
            "-    assert (",
            "-        result == 1",
            "-    )",
            "+    assert result == 1",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_fixture_body_return_changed_expected_value_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            " @pytest.fixture",
            " def sample():",
            "-    return 1",
            "+    return 2",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    finding = findings[0]
    assert finding.file_path == "tests/test_core.py"
    assert finding.line == 12
    assert "pytest fixture" in finding.detail
    assert "return 1" in finding.detail
    assert "ralph/core.py" in finding.detail
    assert "fix:" in finding.detail


def test_fixture_body_return_recorded_reason_suppresses_finding() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            " @pytest.fixture",
            " def sample():",
            f"+{TEST_REASON_CONSTANT}: dict[str, str] = {{",
            '+    "test_core": "updated fixture value for new core behavior",',
            "+}",
            "-    return 1",
            "+    return 2",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_fixture_body_yield_changed_expected_value_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            " @pytest.fixture",
            " def sample():",
            "-    yield 1",
            "+    yield 2",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    finding = findings[0]
    assert finding.file_path == "tests/test_core.py"
    assert finding.line == 12
    assert "pytest fixture" in finding.detail
    assert "yield 1" in finding.detail


def test_fixture_body_yield_recorded_reason_suppresses_finding() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            " @pytest.fixture",
            " def sample():",
            f"+{TEST_REASON_CONSTANT}: dict[str, str] = {{",
            '+    "test_core": "updated fixture yield for new core behavior",',
            "+}",
            "-    yield 1",
            "+    yield 2",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_multiline_fixture_body_return_changed_is_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            " @pytest.fixture",
            " def sample():",
            "     return (",
            "-        1",
            "+        2",
            "     )",
        ),
    )

    findings = audit_test_integrity_diff(diff)

    assert _categories(findings) == {CATEGORY_TEST_WEAKENING}
    finding = findings[0]
    assert finding.file_path == "tests/test_core.py"
    assert finding.line == 13
    assert "pytest fixture" in finding.detail


def test_unrelated_helper_return_in_test_file_is_not_reported() -> None:
    diff = _diff(
        _PRODUCTION_DIFF,
        _test_hunk(
            " def helper():",
            "-    return 1",
            "+    return 2",
        ),
    )

    assert audit_test_integrity_diff(diff) == []


def test_production_code_reading_the_pytest_env_variable_is_reported(tmp_path: Path) -> None:
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
    _write(
        tmp_path,
        "ralph/probe.py",
        'import sys\n\n\ndef in_test() -> bool:\n    return "pytest" in sys.modules\n',
    )

    findings = audit_pytest_detection(tmp_path)

    assert _categories(findings) == {CATEGORY_PYTEST_DETECTION}


def test_production_code_subscripting_sys_modules_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "ralph/probe.py",
        'import sys\n\n\ndef in_test() -> bool:\n    return sys.modules["pytest"] is not None\n',
    )

    findings = audit_pytest_detection(tmp_path)

    assert _categories(findings) == {CATEGORY_PYTEST_DETECTION}


def test_production_code_that_does_not_probe_is_clean(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "ralph/core.py",
        "def run() -> int:\n    return 1\n",
    )

    assert audit_pytest_detection(tmp_path) == []


def test_prose_naming_the_pytest_variable_is_not_a_finding(tmp_path: Path) -> None:
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


def _cli_root(tmp_path: Path) -> Path:
    _write(tmp_path, "ralph/core.py", "def run() -> int:\n    return 1\n")
    return tmp_path


def test_main_returns_one_when_the_diff_weakens_a_test(tmp_path: Path) -> None:
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
    root = _cli_root(tmp_path)
    diff_path = root / "change.diff"
    diff_path.write_text(
        _diff(
            _PRODUCTION_DIFF,
            _test_hunk(
                "     result = run()",
                f"+{TEST_REASON_CONSTANT}: dict[str, str] = {{",
                '+    "test_core": "the contract changed",',
                "+}",
                "-    assert result == 1",
            ),
        ),
        encoding="utf-8",
    )

    assert main(["--diff", str(diff_path), "--root", str(root)]) == 0


def test_main_returns_two_on_an_unknown_argument(tmp_path: Path) -> None:
    assert main(["--nope", str(tmp_path)]) == 2


def test_main_returns_two_on_a_missing_root(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path / "absent")]) == 2


def test_main_returns_two_when_the_root_has_no_production_package(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path)]) == 2


def test_the_gate_never_shells_out_or_touches_the_network() -> None:
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
    root = _cli_root(tmp_path)

    assert main(["--diff", str(root / "absent.diff"), "--root", str(root)]) == 2


def test_main_prints_the_findings_with_file_line_and_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
