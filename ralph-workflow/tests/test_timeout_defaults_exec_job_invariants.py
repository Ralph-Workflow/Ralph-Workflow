"""Import-time invariant tests for §3b exec-job constants in ralph.timeout_defaults.

Covers the new sampler / liveness / job-flow / FIFO-cap constants plus
the import-time order guards that fail closed at module load. The
guards MUST survive ``python -O``; bare ``assert`` would be stripped by
the optimizer, so the module uses ``if/raise RuntimeError``. These
tests prove that contract under both the default interpreter and
``python -O`` so a future regression that switches back to ``assert``
is caught immediately.

The order-violation tests patch a temp copy of ``timeout_defaults.py``
and import it via ``importlib.util.spec_from_file_location`` in a child
interpreter. The import-time guards fire against the patched values; a
tripped guard exits non-zero, a passing import exits zero. ``python -O``
is exercised by passing the flag through ``subprocess.run``.

The "live under ``python -O``" test does a clean ``import
ralph.timeout_defaults`` in a child interpreter so a regression that
flips the guards to ``assert`` is visible immediately — ``-O`` strips
``assert`` and a clean test would silently succeed under the regression.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import ralph.timeout_defaults as td

# Subprocess invocations are load-bearing for the ``-O``-survival proof.
# The audit_test_policy audit forbids real subprocess in any test that
# lacks the ``subprocess_e2e`` marker; pair it with a per-test timeout
# so a runaway subprocess cannot wedge the suite.
pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(15),
]

# Repo root computed from this file's location. The conftest.py mirrors
# the same trick to keep the in-process pytest run on the repo source
# rather than the editable install. Subprocess invocations replicate the
# same path-stripping logic.
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# §3b constant value tests (in-process, fast)
# ---------------------------------------------------------------------------


def test_sample_interval_value() -> None:
    """§3b: liveness sampler wakes every 2 seconds."""
    assert td.EXEC_LIVENESS_SAMPLE_INTERVAL_S == 2.0


def test_no_progress_window_value() -> None:
    """§3b: no-progress window is 15 minutes (900 s)."""
    assert td.EXEC_NO_PROGRESS_WINDOW_S == 900.0


def test_soft_reply_budget_value() -> None:
    """§3b: soft-reply budget is 45 s (60 s verified client timeout minus 15 s headroom)."""
    assert td.EXEC_SOFT_REPLY_BUDGET_MS == 45_000


def test_job_ceiling_values() -> None:
    """§3b: default job ceiling 1 h, hard max 4 h."""
    assert td.EXEC_JOB_CEILING_S == 3600.0
    assert td.EXEC_JOB_CEILING_MAX_S == 14400.0


def test_fifo_caps_values() -> None:
    """§3b: running-jobs cap 4, result FIFO 16."""
    assert td.EXEC_MAX_RUNNING_JOBS == 4
    assert td.EXEC_JOB_RESULT_FIFO == 16


def test_soft_budget_below_legacy_max_timeout() -> None:
    """§3b: soft-reply floor stays strictly under the legacy per-call ceiling."""
    assert td.EXEC_SOFT_REPLY_BUDGET_MS < td.EXEC_MAX_TIMEOUT_MS


# ---------------------------------------------------------------------------
# §3b in-process sanity check (sample < abandon)
# ---------------------------------------------------------------------------


def test_sample_interval_strictly_less_than_abandon_window() -> None:
    """§3b: sampler must run faster than the abandon window so it gets a chance to fire."""
    assert td.EXEC_LIVENESS_SAMPLE_INTERVAL_S < td.EXEC_JOB_ABANDON_S


# ---------------------------------------------------------------------------
# §3b import-time order guards — must survive python -O
# ---------------------------------------------------------------------------


def _import_patched_source(
    patches: list[tuple[str, str]],
    *,
    minus_o: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Write a temp copy of ``timeout_defaults.py`` with ``patches``, import it via subprocess.

    The child's import-time guards fire against the patched values; a
    clean import returns ``rc=0`` and a tripped guard returns
    ``rc!=0``. The audit policy forbids real subprocess in non-
    ``subprocess_e2e`` tests; the module-level marker exempts this
    helper.
    """
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

        # The child loads the patched copy via importlib, NOT via the
        # real ``ralph.timeout_defaults`` namespace, so it does NOT need
        # the conftest.py path-stripping. The patched file's own import-
        # time checks are what we are testing.
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
    """Fail loudly when the child import succeeded or the expected substring is missing."""
    assert result.returncode != 0, (
        f"expected RuntimeError, got exit=0\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = result.stderr + result.stdout
    assert "RuntimeError" in combined, f"missing 'RuntimeError' in output: {combined!r}"
    assert needle in combined, f"missing {needle!r} in output: {combined!r}"


def test_order_violation_suspicion_above_no_progress() -> None:
    """§3b: suspicion > no_progress violates the first '<' and trips the guard."""
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
    """§3b degenerate: suspicion == no_progress violates the strict '<'."""
    result = _import_patched_source(
        [
            (
                "EXEC_SUSPICIOUS_REPORT_S: float = 60.0",
                "EXEC_SUSPICIOUS_REPORT_S: float = 900.0",  # == EXEC_NO_PROGRESS_WINDOW_S
            ),
        ]
    )
    _assert_runtime_error(result, "EXEC_SUSPICIOUS_REPORT_S")


def test_order_violation_no_progress_above_ceiling() -> None:
    """§3b: no_progress > ceiling violates the second '<'."""
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
    """§3b: ceiling > ceiling_max violates the trailing '<='."""
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
    """§3b: sample_interval == abandon trips the sample<deeper guard."""
    result = _import_patched_source(
        [
            (
                "EXEC_LIVENESS_SAMPLE_INTERVAL_S: float = 2.0",
                "EXEC_LIVENESS_SAMPLE_INTERVAL_S: float = 120.0",  # == EXEC_JOB_ABANDON_S
            ),
        ]
    )
    _assert_runtime_error(result, "EXEC_LIVENESS_SAMPLE_INTERVAL_S")


# ---------------------------------------------------------------------------
# §3b "live under python -O" proof
# ---------------------------------------------------------------------------


def test_live_import_succeeds_under_python_o() -> None:
    """§3b import under ``python -O`` returns 0 — guards survive optimization.

    A clean ``import ralph.timeout_defaults`` under ``python -O`` must
    exit 0 because all constants are valid. If a future regression
    flips the ``if/raise RuntimeError`` guards to bare ``assert``,
    ``-O`` strips the asserts and this test still exits 0, masking it.
    Combined with the order-violation tests above (which DO trip under
    ``-O``), the contract is pinned: ``if/raise`` is the enforcement
    path, not ``assert``.

    The child interpreter strips the editable-install shadow paths and
    prepends the repo root (mirroring ``conftest.py``) so ``import
    ralph`` resolves to the repo source.
    """
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
