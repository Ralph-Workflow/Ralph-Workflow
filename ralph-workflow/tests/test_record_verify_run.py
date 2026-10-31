"""``scripts/record_verify_run.py`` records what ``make verify`` actually did.

The receipt is evidence, so a budget the run failed must never be filed as
passed. These tests feed the recorder the exact text ``run_verify`` prints
-- produced by running it against an injected clock, CPU counters and a
stub runner -- and a runner that times out, so no real ``make`` runs.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from ralph import verify as verify_module
from ralph.executor.process import ProcessResult

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "record_verify_run.py"


def _load_recorder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_verify_run", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recorder = _load_recorder()


def _verify_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    cores: int,
    wall: float,
    suite_cpu: float,
) -> tuple[int, str]:
    """Run ``run_verify`` with every tracked step costing ``wall``/``suite_cpu`` in total."""
    clock = [0.0]
    cpu = [0.0]
    per_step = {"test": 0.8, "test-verification-smoke": 0.2}

    def runner(
        command: str,
        args: tuple[str, ...] | list[str] = (),
        **_options: object,
    ) -> ProcessResult:
        share = per_step.get(args[0], 0.0) if command == "make" and args else 0.0
        clock[0] += wall * share
        cpu[0] += suite_cpu * share
        return ProcessResult(command=(command, *args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(verify_module, "sample_suite_resources", lambda: (cpu[0], cpu[0]))
    monkeypatch.setattr(verify_module.os, "cpu_count", lambda: cores)
    code = verify_module.run_verify(cwd=Path(), runner=runner)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _receipt(code: int, output: str) -> dict[str, object]:
    docs = recorder.CommandOutcome(0, "", 1.0, False)
    verify = recorder.CommandOutcome(code, output, 70.0, False)
    receipt: dict[str, object] = recorder.build_receipt(docs, verify)
    return receipt


def _budget_step(receipt: dict[str, object]) -> dict[str, object]:
    per_step = receipt["per_step"]
    assert isinstance(per_step, list)
    steps = [step for step in per_step if isinstance(step, dict)]
    return next(step for step in steps if step["label"] == "combined test budget")


def test_a_budget_the_run_failed_is_recorded_as_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _verify_output(monkeypatch, capsys, cores=12, wall=70.0, suite_cpu=300.0)
    receipt = _receipt(code, output)

    assert receipt["make_exit_code"] == code != 0
    assert receipt["combined_wall_seconds"] == pytest.approx(70.0)
    assert _budget_step(receipt) == {
        "label": "combined test budget",
        "status": "fail",
        "elapsed_seconds": pytest.approx(70.0),
    }


def test_the_combined_budget_line_is_recorded_with_its_cpu_figures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _verify_output(monkeypatch, capsys, cores=4, wall=120.0, suite_cpu=470.0)
    receipt = _receipt(code, output)

    assert code == 0
    assert receipt["budget"] == {
        "status": "warning",
        "wall": pytest.approx(120.0),
        "wall_budget": pytest.approx(60.0),
        "adjusted": pytest.approx(120.0),
        "foreign_cores": pytest.approx(0.0),
        "cores": 4.0,
        "utilisation": pytest.approx(0.98),
        "cpu": pytest.approx(470.0),
        "cpu_budget": pytest.approx(700.0),
    }
    assert _budget_step(receipt)["status"] == "warning"


def test_a_run_inside_the_budget_is_recorded_as_passed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _verify_output(monkeypatch, capsys, cores=12, wall=50.0, suite_cpu=400.0)
    receipt = _receipt(code, output)

    assert code == 0
    assert _budget_step(receipt)["status"] == "pass"


def test_pytest_summaries_that_count_failures_are_recorded_as_failed() -> None:
    output = "=== 2 failed, 40 passed in 3.50s ===\n12 passed, 1 skipped in 0.40s\n"
    receipt = _receipt(2, output)

    assert receipt["per_step"] == [
        {
            "label": "pytest suite: 2 failed, 40 passed",
            "status": "fail",
            "elapsed_seconds": 3.5,
        },
        {
            "label": "pytest suite: 12 passed, 1 skipped",
            "status": "pass",
            "elapsed_seconds": 0.4,
        },
    ]
    assert receipt["budget"] is None
    assert receipt["combined_wall_seconds"] is None


def test_a_command_that_outruns_the_recorder_still_yields_a_receipt(tmp_path: Path) -> None:
    def times_out(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            command, float(str(options["timeout"])), output=b"12 passed in 1.00s\n", stderr=b"hung"
        )

    outcome = recorder.run_command(["make", "verify"], cwd=tmp_path, timeout=5, run=times_out)
    receipt = recorder.build_receipt(recorder.CommandOutcome(0, "", 1.0, False), outcome)

    assert outcome.returncode == recorder.TIMEOUT_EXIT_CODE
    assert outcome.timed_out
    assert "hung" in outcome.output
    assert receipt["make_exit_code"] == recorder.TIMEOUT_EXIT_CODE
    assert receipt["timed_out"] == ["verify"]
