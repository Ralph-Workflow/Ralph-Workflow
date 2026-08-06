"""Manual measurement harness for cross-model plan interoperability."""

from __future__ import annotations

import os

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC

pytestmark = pytest.mark.smoke


def _steps(content: dict[str, object]) -> list[dict[str, object]]:
    raw_steps = content.get("steps")
    if not isinstance(raw_steps, list):
        return []
    return [step for step in raw_steps if isinstance(step, dict)]


def _number_list(value: object) -> list[int]:
    return [item for item in value if isinstance(item, int)] if isinstance(value, list) else []


def _targets(step: dict[str, object]) -> list[str]:
    raw_targets = step.get("targets")
    if not isinstance(raw_targets, list):
        return []
    return [path for target in raw_targets if isinstance(target, dict) if isinstance(path := target.get("path"), str)]


def _report(label: str, model: str, plan: str) -> str:
    content, diagnostics = parse_and_validate(plan, PLAN_SPEC)
    errors = [item for item in diagnostics if item.severity == "error"]
    steps = _steps(content) if not errors else []
    step_ids = [f"S-{number}" for step in steps if isinstance(number := step.get("number"), int)]
    lines = [
        f"## {label} model: {model}",
        f"accepted: {not errors}",
        "stable steps: " + (", ".join(step_ids) or "none"),
    ]
    for step in steps:
        number = step.get("number")
        if not isinstance(number, int):
            continue
        files = ", ".join(_targets(step)) or "none"
        dependencies = ", ".join(f"S-{item}" for item in _number_list(step.get("depends_on"))) or "none"
        lines.append(
            f"- S-{number}: files={files}; dependencies={dependencies}; "
            f"verify={bool(step.get('verify_command'))}; expect={bool(step.get('expected_outcome'))}"
        )
    lines.append(
        "diagnostics: "
        + ("; ".join(f"{item.rule_id}: {item.message}" for item in diagnostics) or "none")
    )
    return "\n".join(lines)


def test_cross_model_plan_measurement_requires_operator_inputs() -> None:
    """Collect safely; an operator supplies recorded plans for comparison."""
    strong_plan = os.environ.get("RALPH_STRONG_MODEL_PLAN")
    small_plan = os.environ.get("RALPH_SMALL_MODEL_PLAN")
    if strong_plan is None or small_plan is None:
        pytest.skip("set RALPH_STRONG_MODEL_PLAN and RALPH_SMALL_MODEL_PLAN to run the manual measurement")

    strong_model = os.environ.get("RALPH_STRONG_MODEL", "operator-supplied")
    small_model = os.environ.get("RALPH_SMALL_MODEL", "operator-supplied")
    print("# Cross-model planning measurement")
    print(_report("strong", strong_model, strong_plan))
    print(_report("small", small_model, small_plan))
    print("cross-execution: operator-driven/not-run")
