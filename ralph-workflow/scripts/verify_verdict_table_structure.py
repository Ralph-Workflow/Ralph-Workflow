#!/usr/bin/env python3

from __future__ import annotations

import re
import subprocess
import sys

_VALID_ROUTINGS = {"S-1", "S-3", "S-4", "S-5"}
_EXPECTED_FOOTER = (
    "ac_count=12 seam_signal=int(512)_WeakSet(0)_RetentionPass(RetentionPassCoordinator)"
)


def _render() -> list[str]:
    proc = subprocess.run(
        [sys.executable, "scripts/render_verdict_table.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.splitlines()


def _phase1_structural() -> str:
    baseline = _render()
    baseline_footer = _footer(baseline)
    from ralph.workspace import awareness

    original = awareness._MAX_DIRTY_PATHS
    awareness._MAX_DIRTY_PATHS = 999
    try:
        patched_lines = _render_with_patch()
    finally:
        awareness._MAX_DIRTY_PATHS = original
    patched_footer = _footer(patched_lines)
    assert baseline_footer != patched_footer, (
        "structural proof failed: footer unchanged after monkey-patch "
        f"(baseline={baseline_footer!r}, patched={patched_footer!r})"
    )
    return f"phase_ok structural_proof {baseline_footer}"


def _render_with_patch() -> list[str]:
    code = (
        "import ralph.workspace.awareness as _a; "
        "_a._MAX_DIRTY_PATHS = 999; "
        "import runpy; "
        "runpy.run_path('scripts/render_verdict_table.py', run_name='__main__')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.splitlines()


def _footer(lines: list[str]) -> str:
    return lines[-1] if lines else ""


def _phase2_header(lines: list[str]) -> str:
    assert len(lines) >= 2, "phase_fail header: fewer than 2 lines"
    assert lines[0] == "| ac | criterion | seam | routing |", (
        f"phase_fail header: line 1 mismatch: {lines[0]!r}"
    )
    assert lines[1] == "|----|----|----|----|", (
        f"phase_fail header: line 2 mismatch: {lines[1]!r}"
    )
    return "phase_ok header"


def _phase3_rows(lines: list[str]) -> str:
    assert len(lines) == 15, f"phase_fail rows: expected 15 lines, got {len(lines)}"
    rows = lines[2:14]
    assert len(rows) == 12, f"phase_fail rows: expected 12 rows, got {len(rows)}"
    seen: set[str] = set()
    row_re = re.compile(r"^\| (AC-\d{2}) \| [^|]+ \| [^|]+ \| (S-\d) \|$")
    for row in rows:
        match = row_re.match(row)
        assert match is not None, f"phase_fail rows: malformed row: {row!r}"
        ac_id = match.group(1)
        routing = match.group(2)
        assert ac_id not in seen, f"phase_fail rows: duplicate {ac_id}"
        seen.add(ac_id)
        assert routing in _VALID_ROUTINGS, (
            f"phase_fail rows: invalid routing {routing!r} for {ac_id}"
        )
    expected_ids = {f"AC-{i:02d}" for i in range(1, 13)}
    assert seen == expected_ids, (
        f"phase_fail rows: ids {seen!r} != expected {expected_ids!r}"
    )
    footer = lines[14]
    assert footer == _EXPECTED_FOOTER, (
        f"phase_fail rows: footer mismatch: {footer!r} != {_EXPECTED_FOOTER!r}"
    )
    return f"phase_ok rows {len(rows)}"


def main() -> int:
    try:
        structural = _phase1_structural()
        lines = _render()
        header = _phase2_header(lines)
        rows = _phase3_rows(lines)
    except AssertionError as exc:
        print(str(exc))
        return 1
    print(structural)
    print(header)
    print(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
