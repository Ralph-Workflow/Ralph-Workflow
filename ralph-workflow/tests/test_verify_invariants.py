"""Negative tests for verify.py invariant enforcement.

Verifies that the module-level RuntimeError checks in ralph.verify
cannot be stripped by ``python -O`` and fire on invariant violations.

Uses subprocess-based tests because the invariants are checked at
import time — modifying module globals after import is not possible
since ``importlib.reload()`` re-executes the full module body.

.. note::

    These tests are marked ``subprocess_e2e`` and excluded from the
    main ``make test`` suite.  In Python 3.14, importing via
    ``importlib.util.spec_from_file_location + exec_module`` triggers a
    ``loguru`` / ``asyncio`` circular import (``AttributeError:
    partially initialized module 'asyncio'``).  This is a test-harness
    compatibility issue, not a verification defect — the invariants
    are still enforced correctly in the main ``make verify`` path.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
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
    verify_path = _get_verify_path()
    repo_root = str(Path(verify_path).parent.parent)
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
            f"sys.path.insert(0, {repo_root!r})\n"
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


def _replace_once(source: str, old: str, new: str) -> str:
    """Replace ``old`` in ``source``, failing loudly when it is absent.

    These tests prove an import-time RuntimeError fires for a patched
    constant. A silent no-op ``str.replace`` would import the PRISTINE
    module instead, so the test would fail with a confusing "expected a
    RuntimeError" rather than naming the real cause: the literal in
    ``ralph/verify.py`` was reformatted and this patcher went stale.
    """
    if old not in source:
        raise AssertionError(
            f"ralph/verify.py no longer contains the patch anchor {old!r};"
            " update this test's anchor to match the current source."
        )
    return source.replace(old, new)


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
    verify_path = _get_verify_path()
    repo_root = str(Path(verify_path).parent.parent)
    original = Path(verify_path).read_text(encoding="utf-8")
    patched = original

    if known_test_step_labels is not None:
        old = (
            "_KNOWN_TEST_STEP_LABELS: frozenset[str] = frozenset(\n"
            '    {"make test", "auto-integrate end-to-end '
            '(make test-auto-integrate-e2e)"}\n'
            ")"
        )
        # Build replacement with the given labels sorted.
        labels_repr = sorted(known_test_step_labels)
        new = f"_KNOWN_TEST_STEP_LABELS: frozenset[str] = frozenset({labels_repr!r})"
        patched = _replace_once(patched, old, new)

    if budget_tracked_steps is not None:
        # Replace the literal defined in ralph/verify.py. Both test
        # steps are tracked, so the source form names the e2e step by
        # its position rather than repeating a bare index.
        old = "_BUDGET_TRACKED_STEPS: frozenset[int] = frozenset({2, len(_VERIFY_STEPS) - 1})"
        new = f"_BUDGET_TRACKED_STEPS: frozenset[int] = frozenset({sorted(budget_tracked_steps)!r})"
        patched = _replace_once(patched, old, new)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="verify_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
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
    result = _run_label_patched_import(known_test_step_labels=frozenset({"other test"}))
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


# ---------------------------------------------------------------------------
# _VERIFY_STEP_TIMEOUT_SECONDS invariant tests
# ---------------------------------------------------------------------------


def _run_step_timeout_patched_import(
    step_timeout_value: float, *, minus_o: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that patches verify.py's _VERIFY_STEP_TIMEOUT_SECONDS.

    Creates a temporary copy of verify.py with the constant replaced,
    then tries to import it. Returns the subprocess result.
    """
    verify_path = _get_verify_path()
    repo_root = str(Path(verify_path).parent.parent)
    original = Path(verify_path).read_text(encoding="utf-8")

    # Patch the _VERIFY_STEP_TIMEOUT_SECONDS constant value.
    patched = original.replace(
        "_VERIFY_STEP_TIMEOUT_SECONDS: Final = 30.0",
        f"_VERIFY_STEP_TIMEOUT_SECONDS: Final = {step_timeout_value}",
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="verify_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
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


def test_verify_step_timeout_must_be_positive() -> None:
    """_VERIFY_STEP_TIMEOUT_SECONDS = 0.0 should raise RuntimeError."""
    result = _run_step_timeout_patched_import(0.0)
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "must be positive" in result.stderr


def test_verify_step_timeout_must_be_minimum() -> None:
    """_VERIFY_STEP_TIMEOUT_SECONDS = 1.0 should raise RuntimeError (below 5.0)."""
    result = _run_step_timeout_patched_import(1.0)
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "must be at least 5.0" in result.stderr


def test_verify_step_timeout_invariant_survives_minus_o() -> None:
    """_VERIFY_STEP_TIMEOUT_SECONDS invariants must survive python -O."""
    result = _run_step_timeout_patched_import(0.0, minus_o=True)
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "must be positive" in result.stderr


# ---------------------------------------------------------------------------
# _INTEGRATION_PER_TEST_TIMEOUT_SECONDS invariant tests
# ---------------------------------------------------------------------------


def _run_integration_timeout_patched_import(
    timeout_value: float, *, minus_o: bool = False
) -> subprocess.CompletedProcess[str]:
    verify_path = _get_verify_path()
    repo_root = str(Path(verify_path).parent.parent)
    original = Path(verify_path).read_text(encoding="utf-8")

    patched = original.replace(
        "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS: Final = 1.0",
        f"_INTEGRATION_PER_TEST_TIMEOUT_SECONDS: Final = {timeout_value}",
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="verify_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
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


def test_integration_per_test_timeout_must_be_1() -> None:
    """_INTEGRATION_PER_TEST_TIMEOUT_SECONDS = 2.0 should raise RuntimeError."""
    result = _run_integration_timeout_patched_import(2.0)
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS must be 1.0" in result.stderr


def test_integration_per_test_timeout_invariant_survives_minus_o() -> None:
    """_INTEGRATION_PER_TEST_TIMEOUT_SECONDS invariant must survive python -O."""
    result = _run_integration_timeout_patched_import(2.0, minus_o=True)
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS must be 1.0" in result.stderr


# ---------------------------------------------------------------------------
# audit_resource_lifecycle containment invariant tests (wt-024 memory-perf AC-05)
# ---------------------------------------------------------------------------


def _run_resource_lifecycle_patched_import(
    *,
    drop_step: bool = False,
    minus_o: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that patches verify.py to remove the
    ``audit_resource_lifecycle`` step label from ``_VERIFY_STEPS`` and
    imports it. Mirrors the pattern at
    ``_run_label_patched_import`` (the per-step label tests above)
    but specifically targets the audit_resource_lifecycle
    containment invariant added in step 8.

    The patch is surgical: the ``resource lifecycle audit`` tuple
    (the only step whose label contains ``audit_resource_lifecycle``)
    is replaced with a label whose ``audit_resource_lifecycle`` substring
    is removed (``"resource lifecycle audit (REMOVED)"``). The
    invariant then fires because no remaining step carries the
    ``audit_resource_lifecycle`` substring.

    The other invariants (_BUDGET_TRACKED_STEPS, _KNOWN_TEST_STEP_LABELS,
    audit_mcp_timeout) are NOT affected because they check different
    subsets of the step labels and the tuple shape is unchanged.
    """
    verify_path = _get_verify_path()
    repo_root = str(Path(verify_path).parent.parent)
    original = Path(verify_path).read_text(encoding="utf-8")

    if drop_step:
        # Replace the resource-lifecycle step's label so the
        # ``audit_resource_lifecycle`` substring is removed. Keep the
        # rest of the tuple (command, args, timeout) intact.
        patched = original.replace(
            '"resource lifecycle audit (audit_resource_lifecycle)"',
            '"resource lifecycle audit (REMOVED)"',
        )
        if patched == original:
            raise AssertionError(
                "patch could not find the resource-lifecycle step label; verify.py may have changed"
            )
    else:
        patched = original

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="verify_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
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


def test_audit_resource_lifecycle_step_must_be_present() -> None:
    """Removing the ``audit_resource_lifecycle`` step label MUST raise
    RuntimeError — the contract cannot be silently dropped.

    Mirrors the audit_mcp_timeout containment invariant added at
    verify.py:317, this guards against a future commit that drops
    the resource-lifecycle step from ``_VERIFY_STEPS`` and reopens
    the unbounded-accumulator / non-daemon-thread / bare-HTTP-client
    leak class.
    """
    result = _run_resource_lifecycle_patched_import(drop_step=True)
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "audit_resource_lifecycle" in result.stderr
    assert "must be present" in result.stderr


def test_audit_resource_lifecycle_invariant_survives_minus_o() -> None:
    """The audit_resource_lifecycle containment invariant must survive
    python -O (if/raise RuntimeError, NOT assert).
    """
    result = _run_resource_lifecycle_patched_import(
        drop_step=True,
        minus_o=True,
    )
    assert result.returncode != 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "RuntimeError" in result.stderr
    assert "audit_resource_lifecycle" in result.stderr


def test_audit_resource_lifecycle_step_present_passes() -> None:
    """Sanity: when the step is NOT removed, the import succeeds cleanly."""
    result = _run_resource_lifecycle_patched_import(drop_step=False)
    assert result.returncode == 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "OK" in result.stdout
