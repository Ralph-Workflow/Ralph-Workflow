"""Tiny dedicated test surface for the descriptive intent_verb hint.

The full set of intent / intent_verb tests lives in
``tests/test_plan_artifact.py``; this file is a focused regression set for the
normalization + free-form vocabulary + empty-string rejection triad that
matters when a planner declares a verb.

The normalization itself lives on the ``Summary`` Pydantic model
(``ralph.mcp.artifacts.plan._summary``). ``normalize_plan_artifact_content``
explicitly does not re-impose field-level validators per its shape-only
contract, so the assertions target the model directly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts.plan._summary import Summary


def _scope_items() -> list[dict[str, str]]:
    return [{"text": "a"}, {"text": "b"}, {"text": "c"}]


def _summary_with_intent_verb(value: object) -> dict[str, object]:
    return {"scope_items": _scope_items(), "intent_verb": value}


def test_intent_verb_lowercased_before_validation() -> None:
    """Mixed case values (e.g. 'Add', 'FIX') are accepted; the value is lowercased."""
    summary = Summary.model_validate(_summary_with_intent_verb("ADD"))
    assert summary.intent_verb == "add"


def test_intent_verb_accepts_project_specific_value() -> None:
    """The descriptive hint has no runtime consumer and accepts new vocabulary."""
    summary = Summary.model_validate(_summary_with_intent_verb("SHIP_IT"))
    assert summary.intent_verb == "ship_it"


def test_intent_verb_rejects_empty_string() -> None:
    """Explicit '' and pure whitespace are rejected.

    Explicit ``""`` is rejected with ``ValueError("intent_verb must not be empty")``
    to distinguish a deliberate empty value from an omitted field (the
    omitted-field path is allowed and yields ``intent_verb=""`` because the
    ``None`` field default round-trips through the before-validator).
    """
    with pytest.raises(ValidationError, match="intent_verb must not be empty"):
        Summary.model_validate(_summary_with_intent_verb(""))
    with pytest.raises(ValidationError, match="intent_verb must not be empty"):
        Summary.model_validate(_summary_with_intent_verb("   "))
