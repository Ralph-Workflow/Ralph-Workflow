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
    context = summary.get("context")
    if context:
        sections.append(f"Summary:\n{context}")

    scope_lines = _bullet_lines(summary.get("scope_items"), "text")
    if scope_lines:
        sections.append("\n".join(["Scope items:", *scope_lines]))

    return "\n\n".join(sections)


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
            f"- {entry}"
            for entry in mcp_values
            if isinstance(entry, str) and entry.strip()
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
