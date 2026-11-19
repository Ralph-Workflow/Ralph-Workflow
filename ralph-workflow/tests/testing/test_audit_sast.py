from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.executor.process import ProcessResult
from ralph.testing import audit_sast
from ralph.testing.audit_sast import SastFinding, audit_sast_roots, diff_findings, main

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ralph.executor.process import ProcessRunOptions
    from ralph.process.manager import ProcessManager

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(60),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "inline-waiver-contract": (
        "the retired expectation asserted that a source comment suppresses a "
        "static-analysis finding; a waiver is a configuration-level entry in "
        "the SAST configuration, so the gate honours the project Bandit "
        "configuration and reports a finding whose source carries a comment"
    ),
    "in-process-sast-invocation": (
        "the process audit forbids subprocess.run in tests, so "
        "test_pre_existing_finding_run_is_warning_free now calls "
        "audit_sast.main in-process; the same three checks are still asserted "
        "(exit code 0 and no WARNING or ERROR on stderr, observed via capsys "
        "instead of a CompletedProcess)"
    ),
}

_ASSERT_PATTERN = "VALUE = 1\nassert VALUE == 1\n"

_COMMENTED_PATTERN = "VALUE = 1\nassert VALUE == 1  # a trailing note\n"

_WAIVER_CONFIG = '[tool.bandit]\nskips = ["B101"]\n'

BanditScanKey = tuple[
    tuple[str, ...],
    tuple[tuple[str, bytes], ...],
    tuple[bool, bytes | None],
]

BanditScanOutcome = tuple[int, str, str]

_SCAN_PATH_SEPARATOR = "--"

_SCAN_CONFIG_FLAG = "-c"


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _scan_config_key(argv: tuple[str, ...]) -> tuple[bool, bytes | None]:
    if _SCAN_CONFIG_FLAG not in argv:
        return (False, None)
    config_index = argv.index(_SCAN_CONFIG_FLAG) + 1
    if config_index >= len(argv):
        return (True, None)
    return (True, Path(argv[config_index]).read_bytes())


def _bandit_scan_key(
    argv: tuple[str, ...],
    scan_root: Path | None,
) -> BanditScanKey | None:
    if scan_root is None or _SCAN_PATH_SEPARATOR not in argv:
        return None
    separator = argv.index(_SCAN_PATH_SEPARATOR)
    relative_paths = argv[separator + 1 :]
    if not relative_paths:
        return None
    try:
        scanned = tuple(
            (relative, (scan_root / relative).read_bytes()) for relative in relative_paths
        )
        config_key = _scan_config_key(argv)
    except OSError:
        return None
    return (relative_paths, scanned, config_key)


@pytest.fixture(autouse=True, scope="session")
def _memoize_identical_bandit_scans() -> Iterator[None]:
    cache: dict[BanditScanKey, BanditScanOutcome] = {}
    spawn_process = audit_sast.run_process

    def _run_process(
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
        _pm: ProcessManager | None = None,
    ) -> ProcessResult:
        argv = (command, *args)
        scan_root = None if options is None else options.cwd
        key = _bandit_scan_key(argv, None if scan_root is None else Path(scan_root))
        if key is None:
            return spawn_process(command, args, options=options, _pm=_pm)
        outcome = cache.get(key)
        if outcome is None:
            completed = spawn_process(command, args, options=options, _pm=_pm)
            cache[key] = (completed.returncode, completed.stdout, completed.stderr)
            return completed
        returncode, stdout, stderr = outcome
        return ProcessResult(command=argv, returncode=returncode, stdout=stdout, stderr=stderr)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(audit_sast, "run_process", _run_process)
        yield


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


def test_pre_existing_finding_run_is_warning_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_tree(tmp_path / "baseline", {"ralph/pkg/mod.py": _ASSERT_PATTERN})
    current = _write_tree(
        tmp_path / "current", {"ralph/pkg/mod.py": "VALUE = 2\nassert VALUE == 1\n"}
    )

    assert main([str(baseline), str(current)]) == 0

    captured = capsys.readouterr()
    assert "WARNING" not in captured.err
    assert "ERROR" not in captured.err


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
