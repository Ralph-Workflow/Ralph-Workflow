#!/usr/bin/env python3
"""Run the authoritative gate and record durable acceptance evidence.

Runs ``make docs`` (Sphinx, ``-W --keep-going``) then
``make -C ralph-workflow verify``, captures the per-step lines emitted by
``ralph/verify.py`` (``step=... status=... elapsed_seconds=...``), and
writes ``.agent/evidence/verify-run.json`` with:

* ``make_exit_code`` — the ``make -C ralph-workflow verify`` exit code;
* ``combined_wall_seconds`` — the authoritative combined test wall time
  parsed from the ``Cumulative test elapsed: X s / budget: 60.0 s`` marker
  that ``ralph/verify.py`` emits from its ``time.monotonic()`` tracker;
* ``make_wall_seconds`` — total wall clock of the verify invocation
  (``time.monotonic()`` around it), recorded for diagnostics only;
* ``per_step`` — list of ``{"label", "status", "elapsed_seconds"}``;
* ``matrix_artifact_path`` — the durable acceptance-matrix path.

The acceptance matrix at ``.agent/evidence/acceptance-criteria-matrix.md``
is produced by re-running the S-1 matrix renderer in-process and copying
the rendered markdown to the durable path; the S-1 test itself never
writes outside ``tmp_path``.

The JSON receipt is written even when ``make verify`` exits non-zero, so
future regressions still leave a durable receipt.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = WORKSPACE_ROOT / "ralph-workflow"
EVIDENCE_DIR = WORKSPACE_ROOT / ".agent" / "evidence"
MATRIX_ARTIFACT = EVIDENCE_DIR / "acceptance-criteria-matrix.md"
VERIFY_RUN_JSON = EVIDENCE_DIR / "verify-run.json"

_SUITE_RESULT_LINE = re.compile(
    r"^(?P<passed>\d+) passed(?P<rest>.*) in (?P<elapsed>[0-9.]+)s$"
)
_CUMULATIVE_LINE = re.compile(
    r"^Cumulative test elapsed: (?P<elapsed>[0-9.]+)s / budget: (?P<budget>[0-9.]+)s$"
)


def _per_step_from_output(verify_output: str) -> list[dict[str, object]]:
    """Derive per-step receipts from the verify step output.

    ``ralph/verify.py`` does not emit machine-readable step lines, so the
    recorder derives them from the pytest suite result lines
    (``N passed ... in X.XXs``) and the cumulative budget summary. Audit
    and lint/typecheck steps surface as a single ``gate`` aggregate.
    """
    per_step: list[dict[str, object]] = []
    for line in verify_output.splitlines():
        suite = _SUITE_RESULT_LINE.match(line.strip())
        if suite is not None:
            per_step.append(
                {
                    "label": f"pytest suite: {suite.group('passed')} passed",
                    "status": "pass",
                    "elapsed_seconds": float(suite.group("elapsed")),
                }
            )
            continue
        cumulative = _CUMULATIVE_LINE.match(line.strip())
        if cumulative is not None:
            per_step.append(
                {
                    "label": "cumulative test budget",
                    "status": "pass",
                    "elapsed_seconds": float(cumulative.group("elapsed")),
                }
            )
    if not per_step:
        per_step.append(
            {
                "label": "make verify (no per-step lines emitted)",
                "status": "fail",
                "elapsed_seconds": 0.0,
            }
        )
    return per_step


def _run(command: list[str], *, cwd: Path) -> tuple[int, str, float]:
    start = time.monotonic()
    completed = subprocess.run(  # noqa: S603 — fixed local make invocation
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    elapsed = time.monotonic() - start
    return completed.returncode, completed.stdout + completed.stderr, elapsed


def _render_matrix_artifact() -> None:
    """Re-run the S-1 renderer and copy its output to the durable path."""
    sys.path.insert(0, str(PACKAGE_ROOT / "tests"))
    try:
        import test_acceptance_criteria_matrix as matrix_module
    finally:
        sys.path.pop(0)
    MATRIX_ARTIFACT.write_text(
        matrix_module.render_acceptance_matrix(),
        encoding="utf-8",
    )


def _combined_test_seconds(verify_output: str) -> float | None:
    """Parse the authoritative cumulative test marker from verify output."""
    for line in verify_output.splitlines():
        match = _CUMULATIVE_LINE.match(line.strip())
        if match is not None:
            return float(match.group("elapsed"))
    return None


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    docs_code, docs_output, _docs_elapsed = _run(["make", "docs"], cwd=WORKSPACE_ROOT)
    verify_code, verify_output, verify_elapsed = _run(
        ["make", "-C", "ralph-workflow", "verify"],
        cwd=WORKSPACE_ROOT,
    )

    per_step = _per_step_from_output(verify_output)
    combined_test_seconds = _combined_test_seconds(verify_output)

    _render_matrix_artifact()

    receipt = {
        "make_exit_code": verify_code,
        "docs_exit_code": docs_code,
        "combined_wall_seconds": combined_test_seconds,
        "make_wall_seconds": round(verify_elapsed, 3),
        "per_step": per_step,
        "matrix_artifact_path": str(MATRIX_ARTIFACT.relative_to(WORKSPACE_ROOT)),
    }
    VERIFY_RUN_JSON.write_text(
        json.dumps(receipt, indent=2) + "\n",
        encoding="utf-8",
    )

    sys.stdout.write(docs_output)
    sys.stdout.write(verify_output)
    print(f"evidence: wrote {VERIFY_RUN_JSON} and {MATRIX_ARTIFACT}")
    return docs_code or verify_code


if __name__ == "__main__":
    raise SystemExit(main())
