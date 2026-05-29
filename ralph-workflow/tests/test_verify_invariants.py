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
        "_TOTAL_TEST_BUDGET_SECONDS: Final = 30.0",
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

        return subprocess.run(  # noqa: PLW1510
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(verify_path).parent.parent),
        )
    finally:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()


# --- Positive: clean import works ---


def test_verify_import_clean_via_subprocess() -> None:
    """Importing verify.py with correct constants (30.0) should succeed."""
    result = _run_patched_import(30.0)
    assert result.returncode == 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "OK" in result.stdout


def test_verify_import_clean_under_minus_o() -> None:
    """Importing verify.py under -O with correct constants should succeed."""
    result = _run_patched_import(30.0, minus_o=True)
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


def test_budget_must_be_30() -> None:
    """_TOTAL_TEST_BUDGET_SECONDS = 31.0 should raise RuntimeError."""
    result = _run_patched_import(31.0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "must be 30.0" in result.stderr


# --- Negative: -O does not strip checks ---


def test_budget_violation_survives_minus_o() -> None:
    """Budget violation must still raise RuntimeError under python -O."""
    result = _run_patched_import(-1.0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "must be positive" in result.stderr
