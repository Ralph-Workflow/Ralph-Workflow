"""Plan artifact formatting for human-readable execution context."""

from __future__ import annotations

import json
from typing import cast


def format_plan_for_execution(content: str) -> str:
    """Convert a plan artifact JSON string into a structured human-readable text block."""
    plan = _parse_plan_content(content)
    if plan is None:
        return content

    sections = [
        _format_summary_section(plan),
        _format_skills_mcp_section(plan),
        _format_steps_section(plan),
        _format_critical_files_section(plan),
        _format_risks_section(plan),
        _format_design_section(plan),
        _format_verification_section(plan),
        _format_work_units_section(plan),
    ]
    return "\n\n".join(section for section in sections if section) or content


def _parse_plan_content(content: str) -> dict[str, object] | None:
    try:
        parsed_obj: object = json.loads(content)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed_obj, dict):
        return None

    parsed = cast("dict[str, object]", parsed_obj)
    plan = parsed.get("content") if parsed.get("type") == "plan" else parsed_obj
    return plan if isinstance(plan, dict) else None


def _format_summary_section(plan: dict[str, object]) -> str:
    summary = plan.get("summary")
    if not isinstance(summary, dict):
        return ""

    sections: list[str] = []
    intent_block = _format_intent_block(summary)
    if intent_block:
        sections.append(intent_block)
    sections.append(_format_context_block(summary))
    scope_block = _format_scope_block(summary)
    if scope_block:
        sections.append(scope_block)

    return "\n\n".join(sections)


def _format_intent_block(summary: dict[str, object]) -> str:
    intent = summary.get("intent")
    intent_verb = summary.get("intent_verb")
    has_intent = isinstance(intent, str) and intent.strip()
    has_verb = isinstance(intent_verb, str) and intent_verb.strip()
    if not (has_intent or has_verb):
        return ""
    intent_lines: list[str] = ["Intent:"]
    if has_verb:
        intent_lines.append(f"verb: {cast('str', intent_verb).strip()}")
    if has_intent:
        intent_lines.append(cast("str", intent).strip())
    return "\n".join(intent_lines)


def _format_context_block(summary: dict[str, object]) -> str:
    context = summary.get("context")
    if isinstance(context, str) and context.strip():
        return f"Summary:\n{context.strip()}"
    return "Summary:\nNo additional context provided."


def _format_scope_block(summary: dict[str, object]) -> str:
    scope_items = summary.get("scope_items")
    if not isinstance(scope_items, list) or not scope_items:
        return ""
    scope_lines: list[str] = []
    for item in scope_items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        category = item.get("category")
        if isinstance(category, str) and category.strip():
            scope_lines.append(f"- {text.strip()} [category]")
        else:
            scope_lines.append(f"- {text.strip()}")
    if not scope_lines:
        return ""
    return "\n".join(["Scope items:", *scope_lines])


def _format_skills_mcp_section(plan: dict[str, object]) -> str:
    skills_mcp = plan.get("skills_mcp")
    if not isinstance(skills_mcp, dict):
        return ""

    sections: list[str] = []
    skill_lines = _bullet_lines(skills_mcp.get("skills"), "self")
    if skill_lines:
        sections.append("\n".join(["Planner-recommended skills:", *skill_lines]))

    mcp_values = skills_mcp.get("mcps")
    if isinstance(mcp_values, list):
        mcp_lines = [
            f"- {entry}" for entry in mcp_values if isinstance(entry, str) and entry.strip()
        ]
        if mcp_lines:
            sections.append("\n".join(["Planner-recommended MCP servers:", *mcp_lines]))

    return "\n\n".join(sections)


def _bullet_lines(items: object, text_key: str) -> list[str]:
    if not isinstance(items, list):
        return []

    if text_key == "self":
        return [f"- {item}" for item in items if isinstance(item, str) and item.strip()]

    return [
        f"- {item[text_key]}" for item in items if isinstance(item, dict) and item.get(text_key)
    ]


def _format_steps_section(plan: dict[str, object]) -> str:
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return ""

    lines = ["Implementation steps:"]
    for step in steps:
        if not isinstance(step, dict):
            continue
        number = step.get("number", "?")
        title = step.get("title", "Untitled step")
        content_text = step.get("content", "")
        lines.append(f"{number}. {title}")
        if content_text:
            lines.append(f"   {content_text}")
    return "\n".join(lines)


def _format_critical_files_section(plan: dict[str, object]) -> str:
    critical_files = plan.get("critical_files")
    if not isinstance(critical_files, dict):
        return ""

    primary_files = critical_files.get("primary_files")
    if not isinstance(primary_files, list) or not primary_files:
        return ""

    lines = ["Critical files:"]
    for file_info in primary_files:
        if not isinstance(file_info, dict):
            continue
        path = file_info.get("path")
        action = file_info.get("action")
        why = file_info.get("why")
        if not (path and action):
            continue
        line = f"- {path} ({action})"
        if why:
            line = f"{line}: {why}"
        lines.append(line)
    return "\n".join(lines)


def _format_risks_section(plan: dict[str, object]) -> str:
    risks = plan.get("risks_mitigations")
    if not isinstance(risks, list) or not risks:
        return ""

    lines = ["Risks and mitigations:"]
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        risk_text = risk.get("risk")
        mitigation = risk.get("mitigation")
        if risk_text and mitigation:
            lines.append(f"- {risk_text} -> {mitigation}")
    return "\n".join(lines)


def _format_verification_section(plan: dict[str, object]) -> str:
    verification = plan.get("verification_strategy")
    if not isinstance(verification, list) or not verification:
        return ""

    lines = ["Verification strategy:"]
    for check in verification:
        if not isinstance(check, dict):
            continue
        method = check.get("method")
        outcome = check.get("expected_outcome")
        if method and outcome:
            lines.append(f"- {method}: {outcome}")
    return "\n".join(lines)


def _format_work_units_section(plan: dict[str, object]) -> str:
    work_units = plan.get("work_units")
    if not isinstance(work_units, list) or not work_units:
        return ""

    lines = ["Work units:"]
    for unit in work_units:
        if not isinstance(unit, dict):
            continue
        unit_id = unit.get("unit_id")
        description = unit.get("description")
        if unit_id and description:
            lines.append(f"- {unit_id}: {description}")
    return "\n".join(lines)


def _format_design_section(plan: dict[str, object]) -> str:
    design = plan.get("design")
    if not isinstance(design, dict):
        return ""

    lines: list[str] = ["Design:"]
    outcome = design.get("outcome")
    if isinstance(outcome, str) and outcome.strip():
        lines.append(f"- Outcome: {outcome.strip()}")
    profile = design.get("planning_profile")
    if isinstance(profile, str) and profile.strip():
        lines.append(f"- planning_profile: {profile.strip()}")
    lines.extend(_format_design_constraints(design))
    lines.extend(_format_design_non_goals(design))
    lines.extend(_format_design_dependency_injection(design))
    lines.extend(_format_design_drift_detection(design))
    lines.extend(_format_design_testability(design))
    lines.extend(_format_design_refactor_strategy(design))
    lines.extend(_format_design_acceptance_criteria(design))
    lines.extend(_format_design_notes(design))

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _format_design_constraints(design: dict[str, object]) -> list[str]:
    constraints = design.get("constraints")
    if not isinstance(constraints, dict):
        return []
    lines: list[str] = []
    text = constraints.get("text")
    if isinstance(text, str) and text.strip():
        lines.append(f"- Design Constraints: {text.strip()}")
    invariants = constraints.get("invariants")
    if isinstance(invariants, list) and invariants:
        lines.extend(
            f"  - invariant: {entry.strip()}"
            for entry in invariants
            if isinstance(entry, str) and entry.strip()
        )
    return lines


def _format_design_non_goals(design: dict[str, object]) -> list[str]:
    non_goals = design.get("non_goals")
    if not isinstance(non_goals, dict):
        return []
    items = non_goals.get("items")
    if not isinstance(items, list):
        return []
    rendered = [item.strip() for item in items if isinstance(item, str) and item.strip()]
    if not rendered:
        return []
    return [f"- Non-Goals: {', '.join(rendered)}"]


def _format_design_dependency_injection(design: dict[str, object]) -> list[str]:
    di = design.get("dependency_injection")
    if not isinstance(di, dict):
        return []
    lines: list[str] = []
    required = di.get("required_for_testability")
    if isinstance(required, bool):
        lines.append(f"- Dependency Injection: required_for_testability={required}")
    forbidden = di.get("forbidden_patterns")
    if isinstance(forbidden, list) and forbidden:
        rendered = [v.strip() for v in forbidden if isinstance(v, str) and v.strip()]
        if rendered:
            lines.append(f"  - forbidden: {', '.join(rendered)}")
    return lines


def _format_design_drift_detection(design: dict[str, object]) -> list[str]:
    drift = design.get("drift_detection")
    if not isinstance(drift, dict):
        return []
    commands = drift.get("guard_commands")
    if not isinstance(commands, list) or not commands:
        return []
    rendered = [c.strip() for c in commands if isinstance(c, str) and c.strip()]
    if not rendered:
        return []
    return [f"- Drift Detection: {', '.join(rendered)}"]


def _format_design_testability(design: dict[str, object]) -> list[str]:
    testability = design.get("testability")
    if not isinstance(testability, dict):
        return []
    lines: list[str] = []
    black_box = testability.get("must_be_black_box")
    if isinstance(black_box, bool):
        lines.append(f"- Testability: must_be_black_box={black_box}")
    forbidden = testability.get("forbidden_in_tests")
    if isinstance(forbidden, list) and forbidden:
        rendered = [v.strip() for v in forbidden if isinstance(v, str) and v.strip()]
        if rendered:
            lines.append(f"  - forbidden_in_tests: {', '.join(rendered)}")
    layers = testability.get("required_test_layers")
    if isinstance(layers, list) and layers:
        rendered = [v.strip() for v in layers if isinstance(v, str) and v.strip()]
        if rendered:
            lines.append(f"  - required_test_layers: {', '.join(rendered)}")
    return lines


def _format_design_refactor_strategy(design: dict[str, object]) -> list[str]:
    refactor = design.get("refactor_strategy")
    if not isinstance(refactor, dict):
        return []
    lines: list[str] = []
    approach = refactor.get("approach")
    if isinstance(approach, str) and approach.strip():
        lines.append(f"- Refactor Strategy: approach={approach.strip()}")
    policy = refactor.get("dead_code_policy")
    if isinstance(policy, str) and policy.strip():
        lines.append(f"  - dead_code_policy: {policy.strip()}")
    return lines


def _format_design_acceptance_criteria(design: dict[str, object]) -> list[str]:
    acceptance = design.get("acceptance_criteria")
    if not isinstance(acceptance, dict):
        return []
    criteria = acceptance.get("criteria")
    if not isinstance(criteria, list):
        return []
    rendered: list[str] = []
    for entry in criteria:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        desc = entry.get("description")
        if isinstance(cid, str) and isinstance(desc, str) and cid.strip() and desc.strip():
            rendered.append(f"{cid.strip()} ({desc.strip()})")
    if not rendered:
        return []
    return [f"- Acceptance Criteria: {', '.join(rendered)}"]


def _format_design_notes(design: dict[str, object]) -> list[str]:
    notes = design.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        return []
    return [f"- Notes: {notes.strip()}"]
