from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ralph.testing.audit_sast import SastFinding, audit_sast_roots, diff_findings, main

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(60),
]

RALPH_TEST_CHANGE_REASONS = {
    "inline-waiver-contract": (
        "the retired expectation asserted that a source comment suppresses a "
        "static-analysis finding; a waiver is a configuration-level entry in "
        "the SAST configuration, so the gate honours the project Bandit "
        "configuration and reports a finding whose source carries a comment"
    ),
    "subprocess-e2e-classification": (
        "the module runs the static-analysis audit CLI as a real child process, "
        "so it carries the subprocess_e2e marker the process audit and the "
        "test-policy audit both require for a real subprocess call; the "
        "required_auto_integrate_e2e marker and the test-suite registry entry "
        "keep the file in the default verification lane, so the black-box CLI "
        "check stays inside the required gate"
    ),
}

_ASSERT_PATTERN = "VALUE = 1\nassert VALUE == 1\n"

_COMMENTED_PATTERN = "VALUE = 1\nassert VALUE == 1  # a trailing note\n"

_WAIVER_CONFIG = '[tool.bandit]\nskips = ["B101"]\n'


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def test_gate_passes_on_a_clean_tree(tmp_path: Path) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": "VALUE = 1\n"})

    findings, preexisting = audit_sast_roots(baseline, current)

    assert findings == []
    assert preexisting == 0


def test_gate_reports_a_new_finding_with_a_fix_hint(tmp_path: Path) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": _ASSERT_PATTERN})

    findings, _ = audit_sast_roots(baseline, current)

    assert [f.test_id for f in findings] == ["B101"]
    assert findings[0].file_path == "ralph/pkg/mod.py"
    assert findings[0].line == 2
    assert findings[0].fix_hint


def test_gate_reports_a_finding_whose_source_carries_a_comment(tmp_path: Path) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": _COMMENTED_PATTERN})

    findings, _ = audit_sast_roots(baseline, current)

    assert [f.test_id for f in findings] == ["B101"]
    assert findings[0].file_path == "ralph/pkg/mod.py"


def test_gate_honours_a_configuration_level_waiver(tmp_path: Path) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(
        tmp_path / "current",
        {"pyproject.toml": _WAIVER_CONFIG, "ralph/pkg/mod.py": _ASSERT_PATTERN},
    )

    findings, _ = audit_sast_roots(baseline, current)

    assert findings == []


def test_gate_reports_a_pre_existing_finding_as_a_baseline_count(tmp_path: Path) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": _ASSERT_PATTERN})
    current = _write_tree(
        tmp_path / "current", {"ralph/pkg/mod.py": "VALUE = 2\nassert VALUE == 1\n"}
    )

    findings, preexisting = audit_sast_roots(baseline, current)

    assert findings == []
    assert preexisting == 1


@pytest.mark.parametrize(
    ("current_source", "expected_exit"),
    [("VALUE = 1\n", 0), (_ASSERT_PATTERN, 1)],
)
def test_command_line_exit_code_reflects_the_new_findings(
    tmp_path: Path,
    current_source: str,
    expected_exit: int,
) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": current_source})

    assert main([str(baseline), str(current)]) == expected_exit


def test_pre_existing_finding_run_is_warning_free(tmp_path: Path) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": _ASSERT_PATTERN})
    current = _write_tree(
        tmp_path / "current", {"ralph/pkg/mod.py": "VALUE = 2\nassert VALUE == 1\n"}
    )

    completed = subprocess.run(
        [sys.executable, "-m", "ralph.testing.audit_sast", str(baseline), str(current)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0
    assert "WARNING" not in completed.stderr
    assert "ERROR" not in completed.stderr


def _make_finding(
    file_path: str,
    test_id: str,
    line: int,
    code_line: str = "assert True",
) -> SastFinding:
    return SastFinding(
        file_path=file_path,
        line=line,
        test_id=test_id,
        test_name="assert_used",
        severity="HIGH",
        message="Use of assert detected",
        fix_hint="hint",
        code_line=code_line,
    )


def test_diff_findings_exact_match_unchanged() -> None:
    f1 = _make_finding("ralph/a.py", "B101", 10, code_line="assert a")
    baseline = [f1]
    current = [f1]
    new, count = diff_findings(baseline, current)
    assert new == []
    assert count == 1


def test_diff_findings_same_file_id_new_line_reported_when_exceeding_baseline() -> None:
    f1 = _make_finding("ralph/a.py", "B101", 10, code_line="assert a")
    f2 = _make_finding("ralph/a.py", "B101", 20, code_line="assert b")
    baseline = [f1]
    current = [f1, f2]
    new, count = diff_findings(baseline, current)
    assert new == [f2]
    assert count == 1


def test_diff_findings_order_invariance() -> None:
    f_orig = _make_finding("ralph/a.py", "B101", 10, code_line="assert orig")
    f_new = _make_finding("ralph/a.py", "B101", 20, code_line="assert new")
    baseline = [f_orig]

    new_order1, count1 = diff_findings(baseline, [f_new, f_orig])
    new_order2, count2 = diff_findings(baseline, [f_orig, f_new])

    assert count1 == count2 == 1
    assert new_order1 == [f_new]
    assert new_order2 == [f_new]
