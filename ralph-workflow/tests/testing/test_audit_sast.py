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

from ralph.testing.audit_sast import audit_sast_roots, main

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
