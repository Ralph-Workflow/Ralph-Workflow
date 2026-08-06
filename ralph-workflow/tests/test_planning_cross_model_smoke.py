"""Manual measurement harness for cross-model plan interoperability."""

from __future__ import annotations

import os

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC

pytestmark = pytest.mark.smoke


def test_cross_model_plan_measurement_requires_operator_inputs() -> None:
    """Collect safely; an operator supplies recorded plans for comparison."""
    strong_plan = os.environ.get("RALPH_STRONG_MODEL_PLAN")
    small_plan = os.environ.get("RALPH_SMALL_MODEL_PLAN")
    if strong_plan is None or small_plan is None:
        pytest.skip("set RALPH_STRONG_MODEL_PLAN and RALPH_SMALL_MODEL_PLAN to run the manual measurement")

    for plan in (strong_plan, small_plan):
        _content, diagnostics = parse_and_validate(plan, PLAN_SPEC)
        assert not [item for item in diagnostics if item.severity == "error"]
