#!/usr/bin/env python3
"""Run the authoritative gate and record durable acceptance evidence.

Runs ``make docs`` (Sphinx, ``-W --keep-going``) then
``make -C ralph-workflow verify``, and writes
``.agent/evidence/verify-run.json`` with:

* ``make_exit_code`` — the ``make -C ralph-workflow verify`` exit code
  (``124`` when it outran this recorder's own timeout);
* ``docs_exit_code`` — the ``make docs`` exit code, same convention;
* ``timed_out`` — the commands this recorder had to kill;
* ``combined_wall_seconds`` — the combined test wall time parsed from the
  ``Cumulative test elapsed: X s / budget: 60.0 s`` line ``ralph/verify.py``
  prints once every step has passed, or ``null`` when it never got there;
* ``budget`` — the verdict and every figure from the
  ``Combined test budget: ...`` line that follows it (wall and
  contention-adjusted wall, foreign and total cores, the suite's
  utilisation, suite CPU and the CPU budget), or ``null``;
* ``make_wall_seconds`` — total wall clock of the verify invocation,
  recorded for diagnostics only;
* ``per_step`` — ``{"label", "status", "elapsed_seconds"}`` entries;
* ``matrix_artifact_path`` — the durable acceptance-matrix path.

``ralph/verify.py`` prints no machine-readable per-step lines, so
``per_step`` is derived from what it does print: each pytest summary line
(``N passed ... in X s`` passes, one that counts failures or errors
fails) and the combined budget. The budget's status comes from the run
itself, never assumed: ``fail`` when verify exited non-zero or printed the
``test budget exhausted`` banner, ``warning`` when it printed a
host-contention or host-capacity WARNING, else ``pass``.

The acceptance matrix at ``.agent/evidence/acceptance-criteria-matrix.md``
is produced by re-running the S-1 matrix renderer in-process and copying
the rendered markdown to the durable path; the S-1 test itself never
writes outside ``tmp_path``.

The JSON receipt is written even when ``make verify`` fails or times out,
so future regressions still leave a durable receipt.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = WORKSPACE_ROOT / "ralph-workflow"
EVIDENCE_DIR = WORKSPACE_ROOT / ".agent" / "evidence"
MATRIX_ARTIFACT = EVIDENCE_DIR / "acceptance-criteria-matrix.md"
VERIFY_RUN_JSON = EVIDENCE_DIR / "verify-run.json"
COMMAND_TIMEOUT_SECONDS = 900
TIMEOUT_EXIT_CODE = 124

_SUITE_RESULT_LINE = re.compile(r"^(?P<summary>\d+ \w+.*) in (?P<elapsed>[0-9.]+)s$")
_SUITE_FAILURE_WORDS = re.compile(r"\b(failed|errors?)\b")
_CUMULATIVE_LINE = re.compile(
    r"^Cumulative test elapsed: (?P<elapsed>[0-9.]+)s / budget: (?P<budget>[0-9.]+)s$"
)
_COMBINED_LINE = re.compile(
    r"^Combined test budget: wall (?P<wall>[0-9.]+)s / (?P<wall_budget>[0-9.]+)s "
    r"\(contention-adjusted (?P<adjusted>[0-9.]+)s: other processes kept "
    r"(?P<foreign_cores>[0-9.]+) of (?P<cores>\d+) cores busy; suite kept "
    r"(?P<utilisation>\d+)% of the free cores busy\), "
    r"suite CPU (?P<cpu>[0-9.]+) / (?P<cpu_budget>[0-9.]+) core-s$"
)
_BUDGET_FAILURE_BANNER = "test budget exhausted"
_BUDGET_WARNING = "WARNING: the tests took longer than the wall-clock budget"


class CommandOutcome(NamedTuple):
    """What one recorded command did."""

    returncode: int
    output: str
    elapsed_seconds: float
    timed_out: bool


def _text(captured: str | bytes | None) -> str:
    if captured is None:
        return ""
    if isinstance(captured, bytes):
        return captured.decode("utf-8", errors="replace")
    return captured


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CommandOutcome:
    """Run ``command``; a timeout becomes exit code 124 with whatever it printed."""
    start = time.monotonic()
    try:
        completed = run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = _text(exc.stdout) + _text(exc.stderr)
        return CommandOutcome(TIMEOUT_EXIT_CODE, output, time.monotonic() - start, True)
    output = _text(completed.stdout) + _text(completed.stderr)
    return CommandOutcome(completed.returncode, output, time.monotonic() - start, False)


def _budget_status(verify_code: int, verify_output: str) -> str:
    if verify_code != 0 or _BUDGET_FAILURE_BANNER in verify_output:
        return "fail"
    if _BUDGET_WARNING in verify_output:
        return "warning"
    return "pass"


def _combined_budget(verify_output: str) -> dict[str, float] | None:
    for line in verify_output.splitlines():
        match = _COMBINED_LINE.match(line.strip())
        if match is not None:
            figures = {name: float(value) for name, value in match.groupdict().items()}
            figures["utilisation"] /= 100.0
            return figures
    return None


def _combined_test_seconds(verify_output: str) -> float | None:
    for line in verify_output.splitlines():
        match = _CUMULATIVE_LINE.match(line.strip())
        if match is not None:
            return float(match.group("elapsed"))
    return None


def _per_step(verify_code: int, verify_output: str) -> list[dict[str, object]]:
    per_step: list[dict[str, object]] = []
    for line in verify_output.splitlines():
        suite = _SUITE_RESULT_LINE.match(line.strip(" =\t"))
        if suite is not None:
            summary = suite.group("summary")
            per_step.append(
                {
                    "label": f"pytest suite: {summary}",
                    "status": "fail" if _SUITE_FAILURE_WORDS.search(summary) else "pass",
                    "elapsed_seconds": float(suite.group("elapsed")),
                }
            )
            continue
        cumulative = _CUMULATIVE_LINE.match(line.strip())
        if cumulative is not None:
            per_step.append(
                {
                    "label": "combined test budget",
                    "status": _budget_status(verify_code, verify_output),
                    "elapsed_seconds": float(cumulative.group("elapsed")),
                }
            )
    if not per_step:
        per_step.append(
            {
                "label": "make verify (no pytest summary or budget line printed)",
                "status": "pass" if verify_code == 0 else "fail",
                "elapsed_seconds": 0.0,
            }
        )
    return per_step


def build_receipt(docs: CommandOutcome, verify: CommandOutcome) -> dict[str, object]:
    """Return the JSON receipt for a ``make docs`` + ``make verify`` run."""
    budget = _combined_budget(verify.output)
    return {
        "make_exit_code": verify.returncode,
        "docs_exit_code": docs.returncode,
        "timed_out": [
            name for name, outcome in (("docs", docs), ("verify", verify)) if outcome.timed_out
        ],
        "combined_wall_seconds": _combined_test_seconds(verify.output),
        "budget": (
            None
            if budget is None
            else {"status": _budget_status(verify.returncode, verify.output), **budget}
        ),
        "make_wall_seconds": round(verify.elapsed_seconds, 3),
        "per_step": _per_step(verify.returncode, verify.output),
        "matrix_artifact_path": str(MATRIX_ARTIFACT.relative_to(WORKSPACE_ROOT)),
    }


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


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    docs = run_command(["make", "docs"], cwd=WORKSPACE_ROOT)
    verify = run_command(["make", "-C", "ralph-workflow", "verify"], cwd=WORKSPACE_ROOT)
    VERIFY_RUN_JSON.write_text(
        json.dumps(build_receipt(docs, verify), indent=2) + "\n",
        encoding="utf-8",
    )

    _render_matrix_artifact()

    sys.stdout.write(docs.output)
    sys.stdout.write(verify.output)
    print(f"evidence: wrote {VERIFY_RUN_JSON} and {MATRIX_ARTIFACT}")
    return docs.returncode or verify.returncode


if __name__ == "__main__":
    raise SystemExit(main())
