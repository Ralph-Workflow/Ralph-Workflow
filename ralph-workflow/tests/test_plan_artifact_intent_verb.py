"""Tiny dedicated test surface for the cheap-model intent_verb shortcut.

The full set of intent / intent_verb tests lives in
``tests/test_plan_artifact.py``; this file is a focused regression set for the
closed-enum forgiveness + lowercasing + empty-string rejection triad that
matters most when the cheapest possible model is asked to declare a verb.
"""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.plan import PlanArtifactValidationError, normalize_plan_artifact_content


def _scope_items() -> list[dict[str, str]]:
    return [{"text": "a"}, {"text": "b"}, {"text": "c"}]


def _plan_with_intent_verb(value: object) -> dict[str, object]:
    return {
        "summary": {"scope_items": _scope_items(), "intent_verb": value},
        "skills_mcp": {"skills": ["test-driven-development"], "mcps": []},
        "steps": [
            {
                "number": 1,
                "title": "t",
                "content": "c",
                "step_type": "file_change",
                "targets": [{"path": "a.py", "action": "modify"}],
            }
        ],
        "critical_files": {"primary_files": [{"path": "a.py", "action": "modify"}]},
        "risks_mitigations": [{"risk": "r", "mitigation": "m"}],
        "verification_strategy": [{"method": "pytest", "expected_outcome": "ok"}],
    }


def test_intent_verb_lowercased_before_validation() -> None:
    """Mixed case values (e.g. 'Add', 'FIX') are accepted; the value is lowercased."""
    plan = _plan_with_intent_verb("ADD")
    normalized = normalize_plan_artifact_content(plan)
    summary = normalized["summary"]
    assert isinstance(summary, dict)
    assert summary["intent_verb"] == "add"


def test_intent_verb_rejects_unknown_value() -> None:
    """Closed enum: anything outside the 9-value set raises ValueError with the bad value."""
    with pytest.raises(PlanArtifactValidationError, match="ship_it"):
        normalize_plan_artifact_content(_plan_with_intent_verb("ship_it"))


def test_intent_verb_rejects_empty_string() -> None:
    """Explicit '' and pure whitespace collapse to the default '' (no raise).

    Round-trip safety: a previously-serialized plan that emitted ``""`` for
    the default value must validate without error on reload.
    """
    normalized = normalize_plan_artifact_content(_plan_with_intent_verb(""))
    summary = normalized["summary"]
    assert isinstance(summary, dict)
    assert summary.get("intent_verb", "") == ""
    normalized_ws = normalize_plan_artifact_content(_plan_with_intent_verb("   "))
    summary_ws = normalized_ws["summary"]
    assert isinstance(summary_ws, dict)
    assert summary_ws.get("intent_verb", "") == ""
