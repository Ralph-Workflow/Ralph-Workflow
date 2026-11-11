"""Black-box coverage for the Bandit baseline-diff static-analysis gate.

The gate is the project's real static-analysis enforcement: it runs the
Bandit engine over the shipped runtime and fails on every finding the
current change introduces. A finding the committed baseline already
carried is pre-existing debt and stays visible as a baseline count.

These tests drive the gate through its public seam (two trees in, the new
findings and the baseline count out) and through its command-line entry
point, so they observe behaviour rather than the gate's internals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing.audit_sast import SastFinding, audit_sast_roots, diff_findings, main

#: The gate spawns the Bandit engine, whose start-up costs more CPU than the
#: default one-second per-test budget allows.
pytestmark = pytest.mark.timeout_seconds(60)

#: A source file that trips Bandit's ``assert_used`` check (B101). The
#: pattern is chosen because it needs no child-process construct, so this
#: test module stays inside the repository's no-direct-subprocess rule.
_ASSERT_PATTERN = "VALUE = 1\nassert VALUE == 1\n"

#: The same finding, waived with the project's documented inline marker.
_WAIVED_PATTERN = "VALUE = 1\nassert VALUE == 1  # nosec: B101 - fixture, not shipped logic\n"


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    """Create *root* and write *files* into it as tree-relative paths."""
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def test_gate_passes_on_a_clean_tree(tmp_path: Path) -> None:
    """A tree whose runtime matches the baseline yields no new finding."""
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": "VALUE = 1\n"})

    findings, preexisting = audit_sast_roots(baseline, current)

    assert findings == []
    assert preexisting == 0


def test_gate_reports_a_new_finding_with_a_fix_hint(tmp_path: Path) -> None:
    """A finding the baseline did not carry is reported with location and hint."""
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": _ASSERT_PATTERN})

    findings, _ = audit_sast_roots(baseline, current)

    assert [f.test_id for f in findings] == ["B101"]
    assert findings[0].file_path == "ralph/pkg/mod.py"
    assert findings[0].line == 2
    assert findings[0].fix_hint


def test_gate_honours_the_inline_nosec_waiver(tmp_path: Path) -> None:
    """A finding waived with an inline ``# nosec`` marker is not reported."""
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": _WAIVED_PATTERN})

    findings, _ = audit_sast_roots(baseline, current)

    assert findings == []


def test_gate_reports_a_pre_existing_finding_as_a_baseline_count(tmp_path: Path) -> None:
    """A finding the baseline already carried stays visible as a count."""
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
    """The gate exits 0 on a clean tree and 1 on a new finding."""
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": "VALUE = 1\n"})
    current = _write_tree(tmp_path / "current", {"ralph/pkg/mod.py": current_source})

    assert main([str(baseline), str(current)]) == expected_exit


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

    # Both [f_new, f_orig] and [f_orig, f_new] must produce exactly [f_new] as the new finding
    new_order1, count1 = diff_findings(baseline, [f_new, f_orig])
    new_order2, count2 = diff_findings(baseline, [f_orig, f_new])

    assert count1 == count2 == 1
    assert new_order1 == [f_new]
    assert new_order2 == [f_new]
