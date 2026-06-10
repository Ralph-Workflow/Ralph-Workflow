"""Tiny dedicated test surface for the plan-level cross-section validator.

The full set of cross-reference tests lives in
``tests/test_plan_artifact.py``; this file is a focused regression set for the
two-link consistency contract between steps and acceptance criteria.
"""

from __future__ import annotations

from typing import cast

import pytest

from ralph.mcp.artifacts.plan import (
    PlanArtifactValidationError,
    normalize_plan_artifact_content,
)


def _valid_plan_with_ac() -> dict[str, object]:
    return {
        "summary": {
            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        },
        "skills_mcp": {"skills": ["test-driven-development"], "mcps": []},
        "steps": [
            {
                "number": 1,
                "title": "First",
                "content": "first",
                "step_type": "file_change",
                "targets": [{"path": "a.py", "action": "modify"}],
            },
            {
                "number": 2,
                "title": "Second",
                "content": "second",
                "step_type": "file_change",
                "targets": [{"path": "b.py", "action": "modify"}],
            },
        ],
        "critical_files": {"primary_files": [{"path": "a.py", "action": "modify"}]},
        "risks_mitigations": [{"risk": "r", "mitigation": "m"}],
        "verification_strategy": [{"method": "pytest", "expected_outcome": "ok"}],
        "design": {
            "acceptance_criteria": {"criteria": [{"id": "AC-01", "description": "first criterion"}]}
        },
    }


def test_cross_section_rejects_orphan_satisfies_id() -> None:
    plan = _valid_plan_with_ac()
    steps = cast("list[dict[str, object]]", plan["steps"])
    steps[0]["satisfies"] = ["AC-99"]
    with pytest.raises(PlanArtifactValidationError, match="unknown acceptance criterion"):
        normalize_plan_artifact_content(plan)


def test_cross_section_rejects_orphan_satisfied_by_steps_number() -> None:
    plan = _valid_plan_with_ac()
    design = cast("dict[str, object]", plan["design"])
    ac = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", ac["criteria"])
    criteria[0]["satisfied_by_steps"] = [99]
    with pytest.raises(PlanArtifactValidationError, match="unknown step number"):
        normalize_plan_artifact_content(plan)


def test_cross_section_accepts_consistent_links() -> None:
    plan = _valid_plan_with_ac()
    steps = cast("list[dict[str, object]]", plan["steps"])
    steps[0]["satisfies"] = ["AC-01"]
    design = cast("dict[str, object]", plan["design"])
    ac = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", ac["criteria"])
    criteria[0]["satisfied_by_steps"] = [1]
    normalized = normalize_plan_artifact_content(plan)
    assert "design" in normalized
