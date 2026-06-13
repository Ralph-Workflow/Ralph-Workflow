"""Tests for the plan-artifact size limits, size guard, and depends_on cycle detector.

This file locks down three new contracts:

1. ``PlanSizeLimits.DEFAULT`` exposes the documented per-cap values that
   the ``## Plan size limits`` section in the format doc references.
2. ``check_plan_size`` is a pure-return helper: it never raises, it
   returns the FIRST violation with ``.field`` / ``.actual`` / ``.cap``
   attributes populated, and the caller
   (``normalize_plan_artifact_content``) is the single point that
   raises ``PlanArtifactValidationError`` when the helper returns a
   non-``None`` error.
3. ``PlanArtifact._validate_depends_on_acyclic`` rejects cyclic
   ``depends_on`` graphs (3-step rings) with a stable message and
   accepts diamond-shaped DAGs (DAG with multiple parents).

The tests use only synthetic in-memory data — no real I/O, no
``time.sleep``, no real subprocess (the dash-O invariant test uses a
bounded subprocess with ``timeout=30``, which the audit policy
explicitly allows for test-harness compatibility).
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

import ralph.mcp.artifacts.plan._size_limits as size_limits_module
from ralph.mcp.artifacts.plan import (
    PLAN_SIZE_LIMITS,
    PlanArtifactSizeError,
    PlanArtifactValidationError,
    PlanSizeLimits,
    PlanStep,
    Summary,
    check_plan_size,
    normalize_plan_artifact_content,
)


def _minimal_plan(
    *,
    steps: list[dict[str, object]] | None = None,
    scope_items: list[dict[str, object]] | None = None,
    acceptance_criteria: list[dict[str, object]] | None = None,
    risks: list[dict[str, object]] | None = None,
    verification: list[dict[str, object]] | None = None,
    primary_files: list[dict[str, object]] | None = None,
    reference_files: list[dict[str, object]] | None = None,
    parallel_plan: list[dict[str, object]] | None = None,
    work_units: list[dict[str, object]] | None = None,
    constraints: dict[str, object] | None = None,
    context: str = "x",
) -> dict[str, object]:
    """Build a minimal but valid plan payload with overridable sections."""
    return {
        "summary": {
            "context": context,
            "scope_items": scope_items or [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        },
        "skills_mcp": {"skills": ["writing-plans"], "mcps": []},
        "steps": steps
        or [{"number": 1, "title": "a", "content": "x", "step_type": "action"}],
        "critical_files": {
            "primary_files": primary_files or [{"path": "x.py", "action": "modify"}],
            "reference_files": reference_files or [],
        },
        "risks_mitigations": risks or [{"risk": "x", "mitigation": "y"}],
        "verification_strategy": verification
        or [{"method": "pytest", "expected_outcome": "ok"}],
        "parallel_plan": parallel_plan or [],
        "work_units": work_units or [],
        "constraints": constraints or {},
    }


def test_default_limits_match_module_constants() -> None:
    """PlanSizeLimits.DEFAULT exposes the documented per-cap values."""
    limits = PlanSizeLimits.DEFAULT
    assert limits.max_total_bytes == 4_000_000
    assert limits.max_steps == 500
    assert limits.max_scope_items == 200
    assert limits.max_acceptance_criteria == 500
    assert limits.max_evidence_per_step == 500
    assert limits.max_risks == 200
    assert limits.max_verification_steps == 100
    assert limits.max_primary_files == 200
    assert limits.max_reference_files == 200
    assert limits.max_parallel_plan_items == 200
    assert limits.max_work_units == 200
    assert limits.max_constraint_list_entries == 500
    assert limits.max_string_short == 1000
    assert limits.max_string_medium == 8000
    assert limits.max_string_long == 20000
    # Class-level alias points to the same instance
    assert PLAN_SIZE_LIMITS is PlanSizeLimits


def test_check_plan_size_accepts_small_plan() -> None:
    """A minimal plan returns None (no violation)."""
    err = check_plan_size(_minimal_plan())
    assert err is None


def test_check_plan_size_rejects_total_byte_overflow() -> None:
    """A payload that serializes to > 4 MB returns a total_bytes violation."""
    big = "x" * 4_200_000
    payload: dict[str, object] = {
        "summary": {
            "context": big,
            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        },
        "skills_mcp": {"skills": ["writing-plans"]},
        "steps": [{"number": 1, "title": "a", "content": "x", "step_type": "action"}],
        "critical_files": {"primary_files": [{"path": "x.py", "action": "modify"}]},
        "risks_mitigations": [{"risk": "x", "mitigation": "y"}],
        "verification_strategy": [{"method": "pytest", "expected_outcome": "ok"}],
    }
    err = check_plan_size(payload)
    assert err is not None
    assert err.field == "total_bytes"
    assert err.actual > 4_000_000
    assert err.cap == 4_000_000
    assert "plan size violation" in str(err)


def test_check_plan_size_rejects_steps_overflow() -> None:
    """501 steps returns a 'steps' violation."""
    plan = _minimal_plan(
        steps=[
            {"number": i, "title": f"s{i}", "content": "x", "step_type": "action"}
            for i in range(1, 502)
        ],
    )
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "steps"
    assert err.actual == 501
    assert err.cap == 500


def test_check_plan_size_rejects_scope_items_overflow() -> None:
    """201 scope_items returns a 'summary.scope_items' violation."""
    plan = _minimal_plan(scope_items=[{"text": f"item-{i}"} for i in range(201)])
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "summary.scope_items"
    assert err.actual == 201
    assert err.cap == 200


def test_check_plan_size_rejects_acceptance_criteria_overflow() -> None:
    """501 acceptance criteria returns the AC violation."""
    ac = [{"id": f"AC-{i:02d}", "description": "x"} for i in range(1, 502)]
    plan = _minimal_plan()
    plan["design"] = {"acceptance_criteria": {"criteria": ac}}
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "design.acceptance_criteria.criteria"
    assert err.actual == 501
    assert err.cap == 500


def test_check_plan_size_rejects_evidence_per_step_overflow() -> None:
    """501 expected_evidence entries on a single step returns the per-step violation."""
    step = {
        "number": 1,
        "title": "a",
        "content": "x",
        "step_type": "action",
        "expected_evidence": [{"kind": "file", "ref": f"f{i}"} for i in range(501)],
    }
    plan = _minimal_plan(steps=[step])
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "steps[*].expected_evidence"
    assert err.actual == 501
    assert err.cap == 500


def test_check_plan_size_rejects_risks_overflow() -> None:
    """201 risks_mitigations returns the risks violation."""
    plan = _minimal_plan(risks=[{"risk": "x", "mitigation": "y"} for _ in range(201)])
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "risks_mitigations"
    assert err.actual == 201
    assert err.cap == 200


def test_check_plan_size_rejects_verification_steps_overflow() -> None:
    """101 verification_strategy entries returns the verification violation."""
    verifications = [
        {"method": "pytest", "expected_outcome": "ok"} for _ in range(101)
    ]
    plan = _minimal_plan(verification=verifications)
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "verification_strategy"
    assert err.actual == 101
    assert err.cap == 100


def test_check_plan_size_rejects_primary_files_overflow() -> None:
    """201 primary_files returns the primary_files violation."""
    files = [{"path": f"f{i}.py", "action": "modify"} for i in range(201)]
    plan = _minimal_plan(primary_files=files)
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "critical_files.primary_files"
    assert err.actual == 201
    assert err.cap == 200


def test_check_plan_size_rejects_constraint_list_overflow() -> None:
    """501 must_not_break entries returns the constraints.must_not_break violation."""
    plan = _minimal_plan(
        constraints={"must_not_break": [f"rule-{i}" for i in range(501)]}
    )
    err = check_plan_size(plan)
    assert err is not None
    assert err.field == "constraints.must_not_break"
    assert err.actual == 501
    assert err.cap == 500


def test_normalize_plan_artifact_content_runs_size_guard_first() -> None:
    """An oversize payload is rejected with the size-violation message."""
    big = "x" * 4_200_000
    plan = _minimal_plan(context=big)
    with pytest.raises(PlanArtifactValidationError) as exc_info:
        normalize_plan_artifact_content(plan)
    message = str(exc_info.value)
    assert "plan size violation" in message
    assert "total_bytes" in message


def test_summary_context_max_8000_chars() -> None:
    """Summary.context is capped at 8000 chars; 8001 raises ValueError."""
    with pytest.raises(ValueError, match="at most 8000"):
        Summary.model_validate(
            {
                "context": "x" * 8001,
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            }
        )


def test_plan_step_content_max_20000_chars() -> None:
    """PlanStep.content is capped at 20000 chars; 20001 raises ValueError."""
    with pytest.raises(ValueError, match="at most 20000"):
        PlanStep.model_validate(
            {"number": 1, "title": "a", "content": "x" * 20001, "step_type": "action"}
        )


def test_evidence_ref_list_max_500() -> None:
    """A step with 501 expected_evidence entries raises ValueError."""
    step = {
        "number": 1,
        "title": "a",
        "content": "x",
        "step_type": "action",
        "expected_evidence": [{"kind": "file", "ref": f"f{i}"} for i in range(501)],
    }
    with pytest.raises(ValueError, match="more than 500"):
        PlanStep.model_validate(step)


def test_most_detailed_plan_round_trip() -> None:
    """A 4-5 kB plan that exercises the largest of every field normalizes cleanly."""
    big_context = "x" * 7500
    big_content = "y" * 19_000
    big_evidence = [{"kind": "file", "ref": f"src/f{i}.py"} for i in range(400)]
    ac = [{"id": f"AC-{i:02d}", "description": f"AC desc {i}"} for i in range(1, 401)]
    plan = _minimal_plan(
        context=big_context,
        scope_items=[{"text": f"item-{i}"} for i in range(150)],
        steps=[
            {
                "number": i,
                "title": f"s{i}",
                "content": big_content,
                "step_type": "action",
                "expected_evidence": big_evidence,
            }
            for i in range(1, 11)
        ],
    )
    plan["design"] = {"acceptance_criteria": {"criteria": ac}}
    result = normalize_plan_artifact_content(plan)
    assert isinstance(result, dict)
    assert "noop" not in result
    assert result.get("steps") is not None


@pytest.mark.subprocess_e2e
def test_size_limits_invariants_survive_python_dash_o() -> None:
    """The import-time RuntimeError checks survive ``python -O``."""
    project_root = Path(__file__).resolve().parents[1]
    check_script = (
        "import importlib, sys; "
        "m = importlib.import_module('ralph.mcp.artifacts.plan._size_limits'); "
        "assert m.PlanSizeLimits.DEFAULT.max_total_bytes == 4000000; "
        "assert m.PlanSizeLimits.DEFAULT.max_steps == 500; "
        "print('OK');"
    )
    completed = subprocess.run(
        [sys.executable, "-O", "-c", check_script],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, (
        f"stderr={completed.stderr!r} stdout={completed.stdout!r}"
    )
    assert "OK" in completed.stdout


def test_noop_short_circuits_size_guard() -> None:
    """A noop payload short-circuits before check_plan_size runs."""
    call_count = {"n": 0}

    def boom(content: object, *, limits: object = None) -> PlanArtifactSizeError | None:
        del content, limits
        call_count["n"] += 1
        return None

    original = size_limits_module.check_plan_size
    size_limits_module.check_plan_size = boom
    try:
        # Both steps and work_units are empty lists -> is_noop_plan returns True
        # and normalize short-circuits BEFORE the size guard runs.
        noop_payload = {
            "summary": {
                "context": "x",
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            },
            "skills_mcp": {"skills": ["writing-plans"]},
            "steps": [],
            "work_units": [],
            "critical_files": {"primary_files": [{"path": "x.py", "action": "modify"}]},
            "risks_mitigations": [{"risk": "x", "mitigation": "y"}],
            "verification_strategy": [{"method": "pytest", "expected_outcome": "ok"}],
        }
        result = normalize_plan_artifact_content(noop_payload)
        assert result == {"noop": True}
        assert call_count["n"] == 0, (
            f"check_plan_size was called {call_count['n']} time(s) for a noop plan; "
            "the noop short-circuit must fire FIRST."
        )
    finally:
        size_limits_module.check_plan_size = original


def test_depends_on_cycle_rejected() -> None:
    """A 3-step ring (1->3, 2->1, 3->2) is rejected with a stable cycle message."""
    plan = _minimal_plan(
        steps=[
            {
                "number": 1,
                "title": "a",
                "content": "x",
                "step_type": "action",
                "depends_on": [3],
            },
            {
                "number": 2,
                "title": "b",
                "content": "x",
                "step_type": "action",
                "depends_on": [1],
            },
            {
                "number": 3,
                "title": "c",
                "content": "x",
                "step_type": "action",
                "depends_on": [2],
            },
        ],
    )
    with pytest.raises(PlanArtifactValidationError) as exc_info:
        normalize_plan_artifact_content(plan)
    message = str(exc_info.value)
    assert re.search(r"plan step depends_on cycle detected at step [123]", message), message


def test_depends_on_diamond_accepted() -> None:
    """A 4-step diamond (1 -> 2, 1 -> 3, 2 -> 4, 3 -> 4) is accepted."""
    plan = _minimal_plan(
        steps=[
            {"number": 1, "title": "a", "content": "x", "step_type": "action"},
            {
                "number": 2,
                "title": "b",
                "content": "x",
                "step_type": "action",
                "depends_on": [1],
            },
            {
                "number": 3,
                "title": "c",
                "content": "x",
                "step_type": "action",
                "depends_on": [1],
            },
            {
                "number": 4,
                "title": "d",
                "content": "x",
                "step_type": "action",
                "depends_on": [2, 3],
            },
        ],
    )
    result = normalize_plan_artifact_content(plan)
    assert isinstance(result, dict)
    assert "noop" not in result
    assert len(result.get("steps", [])) == 4


def test_check_plan_size_never_raises() -> None:
    """check_plan_size is a pure helper — never raises, even on malformed input."""
    source = inspect.getsource(check_plan_size)
    lines = source.splitlines()
    in_docstring = False
    body_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if not in_docstring:
            body_lines.append(line)
    body = "\n".join(body_lines)
    assert "raise " not in body, (
        "check_plan_size must NEVER raise (pure helper contract); "
        f"found raise in body: {body}"
    )
    err = check_plan_size([1, 2, 3])
    assert err is None or isinstance(err, PlanArtifactSizeError)
    huge = ["x" * 1_000_000] * 5
    err2 = check_plan_size(huge)
    assert err2 is not None
    assert err2.field == "total_bytes"
