"""Negative tests for verify.py invariant enforcement.

Verifies that the module-level RuntimeError checks in ralph.verify
cannot be stripped by ``python -O`` and fire on invariant violations.

Uses two batched subprocesses (one normal interpreter, one ``python -O``)
because the invariants are checked at import time — modifying module
globals after import is not possible since ``importlib.reload()``
re-executes the full module body. Each case still gets its own patched
temp copy; the child loops cases in-process so the suite pays two cold
Python startups instead of ~18.

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

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TypedDict

import pytest

# Two cold starts (normal + -O); each child imports many patched copies.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]


def _get_verify_path() -> Path:
    """Return the absolute path to ralph/verify.py."""
    return Path(__file__).parent.parent / "ralph" / "verify.py"


class _CaseSpec(TypedDict):
    name: str
    patches: list[tuple[str, str]]
    minus_o: bool
    expect_ok: bool
    stderr_substrings: list[str]


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
    return source.replace(old, new, 1)


def _invariant_cases() -> list[_CaseSpec]:
    """All import-time invariant cases exercised by this module."""
    empty_labels = "_KNOWN_TEST_STEP_LABELS: frozenset[str] = frozenset([])"
    other_labels = "_KNOWN_TEST_STEP_LABELS: frozenset[str] = frozenset(['other test'])"
    empty_budget_steps = "_BUDGET_TRACKED_STEPS: frozenset[int] = frozenset([])"
    labels_anchor = '_KNOWN_TEST_STEP_LABELS: frozenset[str] = frozenset(\n    {"make test", "make test-multimodal-smoke"}\n)'
    budget_steps_anchor = "_BUDGET_TRACKED_STEPS: frozenset[int] = frozenset({2, len(_VERIFY_STEPS) - 1})"
    budget_anchor = "_TOTAL_TEST_BUDGET_SECONDS: Final = 60.0"
    step_timeout_anchor = "_VERIFY_STEP_TIMEOUT_SECONDS: Final = 30.0"
    integration_anchor = "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS: Final = 1.0"
    resource_label_anchor = '"resource lifecycle audit (audit_resource_lifecycle)"'
    resource_label_gone = '"resource lifecycle audit (REMOVED)"'
    polling_label_anchor = (
        '"filesystem polling/invocation ownership audit (audit_filesystem_polling_invocation)"'
    )
    polling_label_gone = '"filesystem polling/invocation ownership audit (REMOVED)"'
    write_label_anchor = '"filesystem write consolidation audit (audit_filesystem_write_consolidation)"'
    write_label_gone = '"filesystem write consolidation audit (REMOVED)"'
    read_label_anchor = '"filesystem read consolidation audit (audit_filesystem_read_consolidation)"'
    read_label_gone = '"filesystem read consolidation audit (REMOVED)"'

    return [
        {
            "name": "clean_import",
            "patches": [],
            "minus_o": False,
            "expect_ok": True,
            "stderr_substrings": [],
        },
        {
            "name": "clean_import_minus_o",
            "patches": [],
            "minus_o": True,
            "expect_ok": True,
            "stderr_substrings": [],
        },
        {
            "name": "budget_must_be_positive",
            "patches": [(budget_anchor, "_TOTAL_TEST_BUDGET_SECONDS: Final = -1.0")],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "must be positive"],
        },
        {
            "name": "budget_must_be_60",
            "patches": [(budget_anchor, "_TOTAL_TEST_BUDGET_SECONDS: Final = 61.0")],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "must be 60.0"],
        },
        {
            "name": "budget_violation_survives_minus_o",
            "patches": [(budget_anchor, "_TOTAL_TEST_BUDGET_SECONDS: Final = -1.0")],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "must be positive"],
        },
        {
            "name": "known_labels_must_not_be_empty",
            "patches": [(labels_anchor, empty_labels)],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "_KNOWN_TEST_STEP_LABELS must not be empty",
            ],
        },
        {
            "name": "budget_tracked_steps_must_not_be_empty",
            "patches": [(budget_steps_anchor, empty_budget_steps)],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "_BUDGET_TRACKED_STEPS must not be empty",
            ],
        },
        {
            "name": "make_test_must_be_in_known_labels",
            "patches": [(labels_anchor, other_labels)],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "_KNOWN_TEST_STEP_LABELS must contain 'make test'",
            ],
        },
        {
            "name": "label_invariant_survives_minus_o",
            "patches": [(labels_anchor, empty_labels)],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "_KNOWN_TEST_STEP_LABELS must not be empty",
            ],
        },
        {
            "name": "budget_steps_invariant_survives_minus_o",
            "patches": [(budget_steps_anchor, empty_budget_steps)],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "_BUDGET_TRACKED_STEPS must not be empty",
            ],
        },
        {
            "name": "verify_step_timeout_must_be_positive",
            "patches": [(step_timeout_anchor, "_VERIFY_STEP_TIMEOUT_SECONDS: Final = 0.0")],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "must be positive"],
        },
        {
            "name": "verify_step_timeout_must_be_minimum",
            "patches": [(step_timeout_anchor, "_VERIFY_STEP_TIMEOUT_SECONDS: Final = 1.0")],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "must be at least 5.0"],
        },
        {
            "name": "verify_step_timeout_survives_minus_o",
            "patches": [(step_timeout_anchor, "_VERIFY_STEP_TIMEOUT_SECONDS: Final = 0.0")],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "must be positive"],
        },
        {
            "name": "integration_per_test_timeout_must_be_1",
            "patches": [
                (
                    integration_anchor,
                    "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS: Final = 2.0",
                )
            ],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS must be 1.0",
            ],
        },
        {
            "name": "integration_per_test_timeout_survives_minus_o",
            "patches": [
                (
                    integration_anchor,
                    "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS: Final = 2.0",
                )
            ],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "_INTEGRATION_PER_TEST_TIMEOUT_SECONDS must be 1.0",
            ],
        },
        {
            "name": "audit_resource_lifecycle_step_must_be_present",
            "patches": [(resource_label_anchor, resource_label_gone)],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": [
                "RuntimeError",
                "audit_resource_lifecycle",
                "must be present",
            ],
        },
        {
            "name": "audit_resource_lifecycle_survives_minus_o",
            "patches": [(resource_label_anchor, resource_label_gone)],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "audit_resource_lifecycle"],
        },
        {
            "name": "filesystem_polling_invocation_audit_step_must_be_present",
            "patches": [(polling_label_anchor, polling_label_gone)],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "audit_filesystem_polling_invocation"],
        },
        {
            "name": "filesystem_polling_invocation_audit_survives_minus_o",
            "patches": [(polling_label_anchor, polling_label_gone)],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "audit_filesystem_polling_invocation"],
        },
        {
            "name": "filesystem_write_consolidation_audit_step_must_be_present",
            "patches": [(write_label_anchor, write_label_gone)],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "audit_filesystem_write_consolidation"],
        },
        {
            "name": "filesystem_write_consolidation_audit_survives_minus_o",
            "patches": [(write_label_anchor, write_label_gone)],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "audit_filesystem_write_consolidation"],
        },
        {
            "name": "filesystem_read_consolidation_audit_step_must_be_present",
            "patches": [(read_label_anchor, read_label_gone)],
            "minus_o": False,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "audit_filesystem_read_consolidation"],
        },
        {
            "name": "filesystem_read_consolidation_audit_survives_minus_o",
            "patches": [(read_label_anchor, read_label_gone)],
            "minus_o": True,
            "expect_ok": False,
            "stderr_substrings": ["RuntimeError", "audit_filesystem_read_consolidation"],
        },
    ]


def _child_script(verify_path: Path, repo_root: Path, cases: list[_CaseSpec]) -> str:
    """Build a child that imports each patched verify copy in-process."""
    cases_literal = repr(
        [
            {
                "name": case["name"],
                "patches": case["patches"],
                "expect_ok": case["expect_ok"],
                "stderr_substrings": case["stderr_substrings"],
            }
            for case in cases
        ]
    )
    return textwrap.dedent(
        f"""\
        import importlib.util
        import io
        import json
        import sys
        import tempfile
        import traceback
        from contextlib import redirect_stderr
        from pathlib import Path

        verify_path = Path({str(verify_path)!r})
        repo_root = Path({str(repo_root)!r})
        original = verify_path.read_text(encoding="utf-8")
        cases = {cases_literal}
        sys.path.insert(0, str(repo_root))
        results = []

        def replace_once(source, old, new):
            if old not in source:
                raise AssertionError(f"missing patch anchor {{old!r}}")
            return source.replace(old, new, 1)

        for index, case in enumerate(cases):
            patched = original
            try:
                for old, new in case["patches"]:
                    patched = replace_once(patched, old, new)
            except AssertionError as exc:
                results.append({{
                    "name": case["name"],
                    "ok": False,
                    "stderr": str(exc),
                    "patch_error": True,
                    "expect_ok": case["expect_ok"],
                    "stderr_substrings": case["stderr_substrings"],
                }})
                continue

            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                prefix="verify_patched_",
                delete=False,
                encoding="utf-8",
            )
            try:
                tmp.write(patched)
                tmp.flush()
                tmp_path = tmp.name
            finally:
                tmp.close()

            err = io.StringIO()
            ok = False
            stderr_text = ""
            try:
                with redirect_stderr(err):
                    mod_name = f"ralph.verify_invariant_case_{{index}}"
                    spec = importlib.util.spec_from_file_location(mod_name, tmp_path)
                    if spec is None or spec.loader is None:
                        raise RuntimeError("failed to build import spec")
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = mod
                    try:
                        spec.loader.exec_module(mod)
                        ok = True
                    finally:
                        sys.modules.pop(mod_name, None)
            except Exception:
                stderr_text = err.getvalue() + traceback.format_exc()
            else:
                stderr_text = err.getvalue()
            finally:
                Path(tmp_path).unlink(missing_ok=True)

            results.append({{
                "name": case["name"],
                "ok": ok,
                "stderr": stderr_text,
                "patch_error": False,
                "expect_ok": case["expect_ok"],
                "stderr_substrings": case["stderr_substrings"],
            }})

        print("RESULTS_JSON:" + json.dumps(results))
        """
    )


def _run_batch(cases: list[_CaseSpec], *, minus_o: bool) -> list[dict[str, object]]:
    """Run ``cases`` in one child interpreter (optionally under ``-O``)."""
    verify_path = _get_verify_path()
    repo_root = verify_path.parent.parent
    script = _child_script(verify_path, repo_root, cases)
    cmd = [sys.executable]
    if minus_o:
        cmd.append("-O")
    cmd.extend(["-c", script])
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(repo_root),
        check=False,
    )
    assert completed.returncode == 0, (
        f"batch runner failed minus_o={minus_o} rc={completed.returncode}\n"
        f"stdout={completed.stdout}\nstderr={completed.stderr}"
    )
    marker = "RESULTS_JSON:"
    assert marker in completed.stdout, (
        f"missing results marker minus_o={minus_o}\n"
        f"stdout={completed.stdout}\nstderr={completed.stderr}"
    )
    payload = completed.stdout.rsplit(marker, 1)[1].strip().splitlines()[0]
    results: list[dict[str, object]] = json.loads(payload)
    return results


def _assert_results(cases: list[_CaseSpec], results: list[dict[str, object]]) -> None:
    """Assert each case matched its expected import outcome."""
    assert len(results) == len(cases)
    failures: list[str] = []
    for case, result in zip(cases, results, strict=True):
        assert result["name"] == case["name"]
        if result.get("patch_error"):
            failures.append(f"{case['name']}: patch error: {result['stderr']}")
            continue
        if case["expect_ok"]:
            if not result["ok"]:
                failures.append(f"{case['name']}: expected OK import, stderr={result['stderr']!r}")
            continue
        if result["ok"]:
            failures.append(f"{case['name']}: expected RuntimeError, got OK")
            continue
        stderr = str(result["stderr"])
        missing = [s for s in case["stderr_substrings"] if s not in stderr]
        if missing:
            failures.append(f"{case['name']}: missing {missing!r} in stderr={stderr!r}")
    assert not failures, "\n".join(failures)


def test_verify_import_time_invariants_batched() -> None:
    """All verify.py import-time invariants in two cold Python startups.

    Non-``-O`` cases share one interpreter; ``-O`` survival cases share
    another. Each case still patches a fresh temp copy of verify.py.
    """
    verify_path = _get_verify_path()
    original = verify_path.read_text(encoding="utf-8")
    cases = _invariant_cases()
    for case in cases:
        patched = original
        for old, new in case["patches"]:
            patched = _replace_once(patched, old, new)

    normal = [case for case in cases if not case["minus_o"]]
    under_o = [case for case in cases if case["minus_o"]]
    _assert_results(normal, _run_batch(normal, minus_o=False))
    _assert_results(under_o, _run_batch(under_o, minus_o=True))
