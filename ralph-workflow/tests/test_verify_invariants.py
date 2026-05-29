"""Negative tests for verify.py invariant enforcement.

Verifies that the module-level RuntimeError checks in ralph.verify
cannot be stripped by ``python -O`` and fire on invariant violations.

Uses subprocess-based tests because the invariants are checked at
import time — modifying module globals after import is not possible
since ``importlib.reload()`` re-executes the full module body.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess_e2e


def _get_verify_path() -> str:
    """Return the absolute path to ralph/verify.py."""
    # Use relative to this test file
    test_dir = Path(__file__).parent
    return str(test_dir.parent / "ralph" / "verify.py")


def _run_patched_import(
    constant_value: float, *, minus_o: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that patches verify.py's budget constant and imports it.

    Creates a temporary copy of verify.py with the constant replaced,
    then tries to import it. Returns the subprocess result.
    """
    import tempfile

    verify_path = _get_verify_path()
    original = Path(verify_path).read_text(encoding="utf-8")

    # Patch the constant value
    patched = original.replace(
        "_TOTAL_TEST_BUDGET_SECONDS: Final = 60.0",
        f"_TOTAL_TEST_BUDGET_SECONDS: Final = {constant_value}",
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="verify_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        # Create a runner script that imports the patched verify.py
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {Path(tmp_path).parent!r})\n"
            f"sys.path.insert(0, {Path(verify_path).parent!r})\n"
            f"import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('ralph.verify', {tmp_path!r})\n"
            f"mod = importlib.util.module_from_spec(spec)\n"
            f"spec.loader.exec_module(mod)\n"
            "print('OK')\n"
        )

        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", runner])

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(verify_path).parent.parent),
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()


# --- Positive: clean import works ---


def test_verify_import_clean_via_subprocess() -> None:
    """Importing verify.py with correct constants (60.0) should succeed."""
    result = _run_patched_import(60.0)
    assert result.returncode == 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "OK" in result.stdout


def test_verify_import_clean_under_minus_o() -> None:
    """Importing verify.py under -O with correct constants should succeed."""
    result = _run_patched_import(60.0, minus_o=True)
    assert result.returncode == 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "OK" in result.stdout


# --- Negative: budget constant violations ---


def test_budget_must_be_positive() -> None:
    """_TOTAL_TEST_BUDGET_SECONDS = -1.0 should raise RuntimeError."""
    result = _run_patched_import(-1.0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "must be positive" in result.stderr


def test_budget_must_be_60() -> None:
    """_TOTAL_TEST_BUDGET_SECONDS = 61.0 should raise RuntimeError."""
    result = _run_patched_import(61.0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "must be 60.0" in result.stderr


# --- Negative: -O does not strip checks ---


def test_budget_violation_survives_minus_o() -> None:
    """Budget violation must still raise RuntimeError under python -O."""
    result = _run_patched_import(-1.0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "must be positive" in result.stderr


# --- Negative: label and budget integrity invariants ---


def _run_label_patched_import(
    known_test_step_labels: frozenset[str] | None = None,
    budget_tracked_steps: frozenset[int] | None = None,
    *,
    minus_o: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that patches verify.py's label/steps constants and imports it.

    Creates a temporary copy of verify.py with the given constants replaced.
    Only patches the constants that are provided (not None).
    """
    import tempfile

    verify_path = _get_verify_path()
    original = Path(verify_path).read_text(encoding="utf-8")
    patched = original

    if known_test_step_labels is not None:
        old = '_KNOWN_TEST_STEP_LABELS: frozenset[str] = frozenset({"make test"})'
        # Build replacement with the given labels sorted.
        labels_repr = sorted(known_test_step_labels)
        new = f'_KNOWN_TEST_STEP_LABELS: frozenset[str] = frozenset({labels_repr!r})'
        patched = patched.replace(old, new)

    if budget_tracked_steps is not None:
        # Replace: _BUDGET_TRACKED_STEPS: frozenset[int] = frozenset({2})
        old = '_BUDGET_TRACKED_STEPS: frozenset[int] = frozenset({2})'
        new = f'_BUDGET_TRACKED_STEPS: frozenset[int] = frozenset({sorted(budget_tracked_steps)!r})'
        patched = patched.replace(old, new)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="verify_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {Path(tmp_path).parent!r})\n"
            f"sys.path.insert(0, {Path(verify_path).parent!r})\n"
            f"import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('ralph.verify', {tmp_path!r})\n"
            f"mod = importlib.util.module_from_spec(spec)\n"
            f"spec.loader.exec_module(mod)\n"
            "print('OK')\n"
        )

        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", runner])

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(verify_path).parent.parent),
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()


def test_known_test_step_labels_must_not_be_empty() -> None:
    """Empty _KNOWN_TEST_STEP_LABELS should raise RuntimeError."""
    result = _run_label_patched_import(known_test_step_labels=frozenset())
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "_KNOWN_TEST_STEP_LABELS must not be empty" in result.stderr


def test_budget_tracked_steps_must_not_be_empty() -> None:
    """Empty _BUDGET_TRACKED_STEPS should raise RuntimeError."""
    result = _run_label_patched_import(budget_tracked_steps=frozenset())
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "_BUDGET_TRACKED_STEPS must not be empty" in result.stderr


def test_make_test_must_be_in_known_labels() -> None:
    """_KNOWN_TEST_STEP_LABELS without 'make test' should raise RuntimeError."""
    result = _run_label_patched_import(
        known_test_step_labels=frozenset({"other test"})
    )
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "_KNOWN_TEST_STEP_LABELS must contain 'make test'" in result.stderr


def test_label_invariant_survives_minus_o() -> None:
    """Label invariants must still raise RuntimeError under python -O."""
    result = _run_label_patched_import(
        known_test_step_labels=frozenset(),
        minus_o=True,
    )
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "_KNOWN_TEST_STEP_LABELS must not be empty" in result.stderr


def test_budget_steps_invariant_survives_minus_o() -> None:
    """Budget steps invariants must still raise RuntimeError under python -O."""
    result = _run_label_patched_import(
        budget_tracked_steps=frozenset(),
        minus_o=True,
    )
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "_BUDGET_TRACKED_STEPS must not be empty" in result.stderr
