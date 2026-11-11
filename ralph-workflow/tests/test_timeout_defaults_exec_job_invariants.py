from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import ralph.timeout_defaults as td

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(15),
]

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_sample_interval_value() -> None:
    assert td.EXEC_LIVENESS_SAMPLE_INTERVAL_S == 2.0


def test_no_progress_window_value() -> None:
    assert td.EXEC_NO_PROGRESS_WINDOW_S == 900.0


def test_soft_reply_budget_value() -> None:
    assert td.EXEC_SOFT_REPLY_BUDGET_MS == 45_000


def test_job_ceiling_values() -> None:
    assert td.EXEC_JOB_CEILING_S == 3600.0
    assert td.EXEC_JOB_CEILING_MAX_S == 14400.0


def test_fifo_caps_values() -> None:
    assert td.EXEC_MAX_RUNNING_JOBS == 4
    assert td.EXEC_JOB_RESULT_FIFO == 16


def test_soft_budget_below_legacy_max_timeout() -> None:
    assert td.EXEC_SOFT_REPLY_BUDGET_MS < td.EXEC_MAX_TIMEOUT_MS


def test_sample_interval_strictly_less_than_abandon_window() -> None:
    assert td.EXEC_LIVENESS_SAMPLE_INTERVAL_S < td.EXEC_JOB_ABANDON_S


def _import_patched_source(
    patches: list[tuple[str, str]],
    *,
    minus_o: bool = True,
) -> subprocess.CompletedProcess[str]:
    src_path = Path(td.__file__).resolve()
    src = src_path.read_text(encoding="utf-8")
    for old, new in patches:
        if old not in src:
            raise AssertionError(
                f"ralph/timeout_defaults.py no longer contains the patch anchor {old!r}; "
                "update this test's anchor to match the current source."
            )
        src = src.replace(old, new, 1)

    with tempfile.TemporaryDirectory(prefix="td_violation_") as tmp_dir:
        tmp_path = Path(tmp_dir) / "patched_timeout_defaults.py"
        tmp_path.write_text(src, encoding="utf-8")

        script = textwrap.dedent(f"""
            import importlib.util
            import sys
            spec = importlib.util.spec_from_file_location(
                'patched_td', {str(tmp_path)!r},
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("failed to build import spec")
            mod = importlib.util.module_from_spec(spec)
            sys.modules['patched_td'] = mod
            spec.loader.exec_module(mod)
        """)

        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", script])
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )


def _assert_runtime_error(
    result: subprocess.CompletedProcess[str],
    needle: str,
) -> None:
    assert result.returncode != 0, (
        f"expected RuntimeError, got exit=0\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = result.stderr + result.stdout
    assert "RuntimeError" in combined, f"missing 'RuntimeError' in output: {combined!r}"
    assert needle in combined, f"missing {needle!r} in output: {combined!r}"


def test_order_violation_suspicion_above_no_progress() -> None:
    result = _import_patched_source(
        [
            (
                "EXEC_SUSPICIOUS_REPORT_S: float = 60.0",
                "EXEC_SUSPICIOUS_REPORT_S: float = 1200.0",
            ),
        ]
    )
    _assert_runtime_error(result, "EXEC_SUSPICIOUS_REPORT_S")


def test_order_violation_suspicion_equals_no_progress() -> None:
    result = _import_patched_source(
        [
            (
                "EXEC_SUSPICIOUS_REPORT_S: float = 60.0",
                "EXEC_SUSPICIOUS_REPORT_S: float = 900.0",
            ),
        ]
    )
    _assert_runtime_error(result, "EXEC_SUSPICIOUS_REPORT_S")


def test_order_violation_no_progress_above_ceiling() -> None:
    result = _import_patched_source(
        [
            (
                "EXEC_NO_PROGRESS_WINDOW_S: float = 900.0",
                "EXEC_NO_PROGRESS_WINDOW_S: float = 4000.0",
            ),
        ]
    )
    _assert_runtime_error(result, "EXEC_NO_PROGRESS_WINDOW_S")


def test_order_violation_ceiling_above_ceiling_max() -> None:
    result = _import_patched_source(
        [
            (
                "EXEC_JOB_CEILING_S: float = 3600.0",
                "EXEC_JOB_CEILING_S: float = 20000.0",
            ),
        ]
    )
    _assert_runtime_error(result, "EXEC_JOB_CEILING_S")


def test_order_violation_sample_interval_equals_abandon() -> None:
    result = _import_patched_source(
        [
            (
                "EXEC_LIVENESS_SAMPLE_INTERVAL_S: float = 2.0",
                "EXEC_LIVENESS_SAMPLE_INTERVAL_S: float = 120.0",
            ),
        ]
    )
    _assert_runtime_error(result, "EXEC_LIVENESS_SAMPLE_INTERVAL_S")


def test_live_import_succeeds_under_python_o() -> None:
    script = textwrap.dedent(f"""
        import sys
        # Mirror conftest.py path-stripping: the harness has an editable
        # install that shadows the repo source; strip its entries and
        # prepend REPO_ROOT so ``import ralph`` resolves to the repo.
        _pythonpath_entries = frozenset(
            entry
            for entry in __import__('os').environ.get('PYTHONPATH', '').split(__import__('os').sep)
            if entry
        )
        if _pythonpath_entries:
            sys.path[:] = [p for p in sys.path if p not in _pythonpath_entries]
        sys.path.insert(0, {str(REPO_ROOT)!r})
        import ralph.timeout_defaults as td
        # Sanity: §3b constants are present. The contract the parent
        # checks is "the import succeeded and rc == 0", which fails
        # closed under -O when guards are correctly implemented.
        _ = td.EXEC_LIVENESS_SAMPLE_INTERVAL_S
        _ = td.EXEC_SOFT_REPLY_BUDGET_MS
    """)
    result = subprocess.run(
        [sys.executable, "-O", "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, (
        f"import failed under python -O\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_timing_safety_invariants_survive_minus_o() -> None:
    timing_safety_path = Path("ralph/mcp/server/_timing_safety.py")
    original = timing_safety_path.read_text(encoding="utf-8")
    anchor = "TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = 30_000"
    if anchor not in original:
        raise AssertionError(
            "timing-safety headroom anchor missing; _timing_safety.py was reformatted."
        )

    script = textwrap.dedent(
        """\
        import importlib.util
        import io
        import sys
        import tempfile
        import traceback
        from contextlib import redirect_stderr
        from pathlib import Path

        path = Path({path!r})
        marker = "TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = -1"
        patched = path.read_text(encoding="utf-8").replace(
            "TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = 30_000",
            marker,
            1,
        )
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            tmp.write(patched)
            tmp.flush()
            tmp_path = tmp.name
        finally:
            tmp.close()

        err = io.StringIO()
        try:
            with redirect_stderr(err):
                spec = importlib.util.spec_from_file_location("timing_safety_case", tmp_path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["timing_safety_case"] = mod
                spec.loader.exec_module(mod)
            ok = True
            stderr = err.getvalue()
        except Exception:
            ok = False
            stderr = err.getvalue() + traceback.format_exc()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        print("OK:" + str(ok))
        print("STDERR:" + stderr)
        """
    )

    script_with_paths = script.format(path=str(timing_safety_path))
    for minus_o in (False, True):
        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", script_with_paths])
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert "OK:False" in completed.stdout, (
            f"expected RuntimeError on import (minus_o={minus_o}); "
            f"got stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
        assert "RuntimeError" in completed.stdout, (
            f"expected RuntimeError text (minus_o={minus_o}); got stdout={completed.stdout!r}"
        )
        assert "TIMING_SAFETY_TRANSPORT_HEADROOM_MS" in completed.stdout, (
            f"expected the invariant name in stderr (minus_o={minus_o}); "
            f"got stdout={completed.stdout!r}"
        )
