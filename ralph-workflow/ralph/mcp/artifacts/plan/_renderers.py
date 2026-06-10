"""Markdown renderer and envelope-aware payload extractor.

``render_plan_markdown`` produces the agent-facing Markdown handoff
(``.agent/PLAN.md``). ``extract_plan_payload`` normalizes an artifact
envelope (``{"type": "plan", "content": {...}}``) or a bare plan
dict into the canonical plan dict; ``extract_plan_skill_names`` reads
planner-specified skill names from that canonical dict.

The per-section ``_render_*_block`` helpers keep the high-level
``render_plan_markdown`` linear and easy to skim. ``_string_list``
normalizes a JSON-decoded list-of-strings into stripped non-empty
strings, which is the shape the renderer needs to bullet-list planner
skills and MCP servers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan._section_registry import PLAN_MARKDOWN_PATH
from ralph.mcp.artifacts.plan._validation import (
    normalize_plan_artifact_content,
    parse_plan_payload_lenient,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ralph.mcp.artifacts.plan._section_models import PlanArtifactDict


def render_plan_markdown(content: Mapping[str, object]) -> str:
    """Render the structured plan artifact as agent-facing Markdown."""
    plan = normalize_plan_artifact_content(cast("dict[str, object]", dict(content)))
    if plan.get("noop") is True:
        return "# Execution Plan\n\nNo execution work is required.\n"

    lines = ["# Execution Plan"]
    lines.extend(_render_summary_section(plan.get("summary")))
    lines.extend(_render_skills_mcp_section(plan.get("skills_mcp")))
    lines.extend(_render_steps_section(plan.get("steps")))
    lines.extend(_render_critical_files_section(plan.get("critical_files")))
    lines.extend(_render_constraints_section(plan.get("constraints")))
    lines.extend(_render_risks_section(plan.get("risks_mitigations")))
    lines.extend(_render_design_section(plan.get("design")))
    lines.extend(_render_verification_section(plan.get("verification_strategy")))
    lines.extend(_render_parallel_plan_section(plan.get("parallel_plan")))
    lines.extend(_render_work_units_section(plan.get("work_units")))
    return "\n".join(lines).rstrip() + "\n"


def _render_summary_section(summary: object) -> list[str]:
    if not isinstance(summary, dict):
        return []

    lines: list[str] = []
    intent = summary.get("intent")
    intent_verb = summary.get("intent_verb")
    has_intent = isinstance(intent, str) and intent.strip()
    has_verb = isinstance(intent_verb, str) and intent_verb.strip()
    if has_intent or has_verb:
        lines.extend(["", "## Intent", ""])
        if has_verb:
            lines.extend(["", f"verb: {cast('str', intent_verb).strip()}"])
        if has_intent:
            lines.extend(["", cast("str", intent).strip()])

    context = summary.get("context")
    if isinstance(context, str) and context.strip():
        lines.extend(["", "## Summary", "", context.strip()])
    else:
        lines.extend(["", "## Summary", "", "No additional context provided."])

    scope_items = summary.get("scope_items")
    if isinstance(scope_items, list) and scope_items:
        lines.extend(["", "## Scope"])
        for item in scope_items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                category = item.get("category")
                if isinstance(category, str) and category.strip():
                    lines.extend(["", f"- {text.strip()} [category]"])
                else:
                    lines.extend(["", f"- {text.strip()}"])
    return lines


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [entry.strip() for entry in value if isinstance(entry, str) and entry.strip()]


def _render_skills_mcp_section(skills_mcp: object) -> list[str]:
    if not isinstance(skills_mcp, dict):
        return []

    skill_lines = [f"- `{name}`" for name in _string_list(skills_mcp.get("skills"))]
    mcp_lines = [f"- `{name}`" for name in _string_list(skills_mcp.get("mcps"))]
    if not skill_lines and not mcp_lines:
        return []

    lines = ["", "## Skills and MCPs"]
    if skill_lines:
        lines.extend(["", "### Skills", ""])
        lines.extend(skill_lines)
    if mcp_lines:
        lines.extend(["", "### MCP Servers", ""])
        lines.extend(mcp_lines)
    return lines


def extract_plan_payload(content: Mapping[str, object]) -> PlanArtifactDict | None:
    """Return the raw plan dict from an artifact envelope or bare payload.

    Thin wrapper around ``parse_plan_payload_lenient`` that preserves the
    None-on-failure return path expected by ``extract_plan_skill_names``.
    """
    return parse_plan_payload_lenient(content)


def extract_plan_skill_names(content: Mapping[str, object]) -> tuple[str, ...]:
    """Return planner-specified skill names from a plan artifact payload."""
    plan = extract_plan_payload(content)
    if plan is None:
        return ()
    skills_mcp = plan.get("skills_mcp")
    if not isinstance(skills_mcp, dict):
        return ()
    skills = skills_mcp.get("skills")
    if not isinstance(skills, list):
        return ()
    names = tuple(entry.strip() for entry in skills if isinstance(entry, str) and entry.strip())
    return names


def _render_steps_section(steps: object) -> list[str]:
    if not isinstance(steps, list) or not steps:
        return []

    lines = ["", "## Steps"]
    for step in steps:
        if not isinstance(step, dict):
            continue
        lines.extend(_render_step_entry(step))
    return lines


def _render_step_entry(step: Mapping[str, object]) -> list[str]:  # noqa: PLR0912 - per-step field rendering
    number = step.get("number", "?")
    title = step.get("title", "Untitled step")
    lines = ["", f"{number}. **{title}**"]
    content_text = step.get("content")
    if isinstance(content_text, str) and content_text.strip():
        lines.extend(["", f"   {content_text.strip()}"])

    targets = step.get("targets")
    if isinstance(targets, list) and targets:
        lines.extend(["", "   Targets:"])
        for target in targets:
            if not isinstance(target, dict):
                continue
            path = target.get("path")
            action = target.get("action")
            if isinstance(path, str) and isinstance(action, str):
                lines.extend(["", f"   - `{path}` ({action})"])

    step_type = step.get("step_type")
    if isinstance(step_type, str) and step_type and step_type != "action":
        lines.extend(["", f"   - step_type: {step_type}"])
    priority = step.get("priority")
    if isinstance(priority, str) and priority.strip():
        lines.extend(["", f"   - priority: {priority.strip()}"])
    depends_on = step.get("depends_on")
    if isinstance(depends_on, list) and depends_on:
        rendered = [str(d) for d in depends_on if isinstance(d, (int, str))]
        if rendered:
            lines.extend(["", f"   - depends_on: [{', '.join(rendered)}]"])
    satisfies = step.get("satisfies")
    if isinstance(satisfies, list) and satisfies:
        rendered = [str(s) for s in satisfies if isinstance(s, str) and s.strip()]
        if rendered:
            lines.extend(["", f"   - satisfies: [{', '.join(rendered)}]"])
    expected_evidence = step.get("expected_evidence")
    if isinstance(expected_evidence, list) and expected_evidence:
        rendered = [_format_evidence_ref(entry) for entry in expected_evidence]
        rendered = [r for r in rendered if r]
        if rendered:
            lines.extend(["", "   - expected_evidence:"])
            for entry in rendered:
                lines.extend(["", f"     - {entry}"])
    verify_command = step.get("verify_command")
    if isinstance(verify_command, str) and verify_command.strip():
        lines.extend(["", f"   - verify_command: `{verify_command.strip()}`"])
    location = step.get("location")
    if isinstance(location, str) and location.strip():
        lines.extend(["", f"   - location: `{location.strip()}`"])
    return lines


def _format_evidence_ref(ref: object) -> str:
    """Render an ``EvidenceRef`` (or dict/legacy string) as ``kind: ref``.

    Accepts EvidenceRef instances, plain dicts with ``kind`` / ``ref``
    keys, and bare strings (treated as ``kind='file'`` for legacy
    compatibility). Returns an empty string for unrecognized shapes.
    """
    if isinstance(ref, str):
        stripped = ref.strip()
        if not stripped:
            return ""
        return f"file: {stripped}"
    kind: object = getattr(ref, "kind", None)
    ref_value: object = getattr(ref, "ref", None)
    if kind is None and isinstance(ref, dict):
        kind = ref.get("kind")
        ref_value = ref.get("ref")
    if not isinstance(kind, str) or not isinstance(ref_value, str):
        return ""
    stripped_ref = ref_value.strip()
    if not stripped_ref:
        return ""
    return f"{kind}: {stripped_ref}"


def _constraints_section_empty(payload: object) -> bool:
    """Return True when the constraints payload carries no displayable content."""
    if not isinstance(payload, dict):
        return True
    for key in ("must_not_break", "must_keep_working"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return False
    for key in ("performance_budget", "security_posture"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return False
    return True


def _render_constraints_section(constraints: object) -> list[str]:
    """Render the top-level Project Constraints section, or [] when empty."""
    if _constraints_section_empty(constraints):
        return []
    assert isinstance(constraints, dict)
    lines = ["", "## Project Constraints"]
    must_not_break = constraints.get("must_not_break")
    if isinstance(must_not_break, list) and must_not_break:
        lines.extend(["", "- must_not_break:"])
        for entry in must_not_break:
            if isinstance(entry, str) and entry.strip():
                lines.extend(["", f"  - {entry.strip()}"])
    must_keep_working = constraints.get("must_keep_working")
    if isinstance(must_keep_working, list) and must_keep_working:
        lines.extend(["", "- must_keep_working:"])
        for entry in must_keep_working:
            if isinstance(entry, str) and entry.strip():
                lines.extend(["", f"  - {entry.strip()}"])
    performance_budget = constraints.get("performance_budget")
    if isinstance(performance_budget, str) and performance_budget.strip():
        lines.extend(["", f"- performance_budget: {performance_budget.strip()}"])
    security_posture = constraints.get("security_posture")
    if isinstance(security_posture, str) and security_posture.strip():
        lines.extend(["", f"- security_posture: {security_posture.strip()}"])
    return lines


def _render_critical_files_section(critical_files: object) -> list[str]:
    if not isinstance(critical_files, dict):
        return []
    primary_files = critical_files.get("primary_files")
    if not isinstance(primary_files, list) or not primary_files:
        return []

    lines = ["", "## Critical Files"]
    for file_info in primary_files:
        if not isinstance(file_info, dict):
            continue
        path = file_info.get("path")
        action = file_info.get("action")
        if isinstance(path, str) and isinstance(action, str):
            lines.extend(["", f"- `{path}` ({action})"])
    return lines


def _render_risks_section(risks: object) -> list[str]:
    if not isinstance(risks, list) or not risks:
        return []

    lines = ["", "## Risks and Mitigations"]
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        risk_text = risk.get("risk")
        mitigation = risk.get("mitigation")
        if isinstance(risk_text, str) and isinstance(mitigation, str):
            lines.extend(["", f"- **Risk:** {risk_text}", f"  **Mitigation:** {mitigation}"])
    return lines


def _render_design_section(design: object) -> list[str]:
    if not isinstance(design, dict):
        return []

    sub_section_order: tuple[tuple[str, str, Callable[[Mapping[str, object]], list[str]]], ...] = (
        ("constraints", "Design Constraints", _render_design_constraints_block),
        ("non_goals", "Non-Goals", _render_design_non_goals_block),
        ("dependency_injection", "Dependency Injection", _render_design_di_block),
        ("drift_detection", "Drift Detection", _render_design_drift_block),
        ("testability", "Testability", _render_design_testability_block),
        ("refactor_strategy", "Refactor Strategy", _render_design_refactor_block),
        ("acceptance_criteria", "Acceptance Criteria", _render_design_acceptance_block),
    )

    rendered: list[str] = []
    outcome = design.get("outcome")
    if isinstance(outcome, str) and outcome.strip():
        rendered.append("### Outcome")
        rendered.append("")
        rendered.append(outcome.strip())

    profile = design.get("planning_profile")
    if isinstance(profile, str) and profile.strip():
        rendered.append(f"- planning_profile: {profile.strip()}")

    for key, heading, renderer in sub_section_order:
        sub = design.get(key)
        if not isinstance(sub, dict):
            continue
        sub_lines = renderer(cast("Mapping[str, object]", sub))
        if not sub_lines:
            continue
        rendered.append("")
        rendered.append(f"### {heading}")
        rendered.append("")
        rendered.extend(sub_lines)

    notes = design.get("notes")
    if isinstance(notes, str) and notes.strip():
        rendered.extend(["", "### Notes", "", notes.strip()])

    if not rendered:
        return []
    rendered.insert(0, "")
    rendered.insert(1, "## Design")
    return rendered


def _render_design_constraints_block(constraints: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    text = constraints.get("text")
    if isinstance(text, str) and text.strip():
        lines.append(f"- text: {text.strip()}")
    invariants = constraints.get("invariants")
    if isinstance(invariants, list):
        lines.extend(
            f"  - invariant: {entry.strip()}"
            for entry in invariants
            if isinstance(entry, str) and entry.strip()
        )
    style = constraints.get("architecture_style")
    if isinstance(style, str) and style.strip():
        lines.append(f"- architecture_style: {style.strip()}")
    return lines


def _render_design_non_goals_block(non_goals: Mapping[str, object]) -> list[str]:
    items = non_goals.get("items")
    if not isinstance(items, list):
        return []
    return [f"- {entry.strip()}" for entry in items if isinstance(entry, str) and entry.strip()]


def _render_design_di_block(di: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    required = di.get("required_for_testability")
    if isinstance(required, bool):
        lines.append(f"- required_for_testability: {'true' if required else 'false'}")
    preferred = di.get("preferred_patterns")
    if isinstance(preferred, list):
        lines.extend(
            f"  - preferred_pattern: {entry.strip()}"
            for entry in preferred
            if isinstance(entry, str) and entry.strip()
        )
    forbidden = di.get("forbidden_patterns")
    if isinstance(forbidden, list):
        lines.extend(
            f"  - forbidden_pattern: {entry.strip()}"
            for entry in forbidden
            if isinstance(entry, str) and entry.strip()
        )
    notes = di.get("notes")
    if isinstance(notes, str) and notes.strip():
        lines.append(f"- notes: {notes.strip()}")
    return lines


def _render_design_drift_block(drift: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    commands = drift.get("guard_commands")
    if isinstance(commands, list):
        lines.extend(
            f"- guard_command: `{entry.strip()}`"
            for entry in commands
            if isinstance(entry, str) and entry.strip()
        )
    expected = drift.get("expected_outputs")
    if isinstance(expected, list):
        lines.extend(
            f"  - expected_output: {entry.strip()}"
            for entry in expected
            if isinstance(entry, str) and entry.strip()
        )
    sources = drift.get("sources")
    if isinstance(sources, list):
        lines.extend(
            f"  - source: {entry.strip()}"
            for entry in sources
            if isinstance(entry, str) and entry.strip()
        )
    action = drift.get("on_drift_action")
    if isinstance(action, str) and action.strip():
        lines.append(f"- on_drift_action: {action.strip()}")
    return lines


def _render_design_testability_block(testability: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    black_box = testability.get("must_be_black_box")
    if isinstance(black_box, bool):
        lines.append(f"- must_be_black_box: {'true' if black_box else 'false'}")
    forbidden = testability.get("forbidden_in_tests")
    if isinstance(forbidden, list):
        lines.extend(
            f"  - forbidden_in_tests: {entry.strip()}"
            for entry in forbidden
            if isinstance(entry, str) and entry.strip()
        )
    layers = testability.get("required_test_layers")
    if isinstance(layers, list):
        lines.extend(
            f"  - required_test_layer: {entry.strip()}"
            for entry in layers
            if isinstance(entry, str) and entry.strip()
        )
    clock_required = testability.get("clock_injection_required")
    if isinstance(clock_required, bool):
        lines.append(f"- clock_injection_required: {'true' if clock_required else 'false'}")
    max_unit = testability.get("max_unit_test_seconds")
    if isinstance(max_unit, (int, float)) and not isinstance(max_unit, bool):
        lines.append(f"- max_unit_test_seconds: {float(max_unit):g}")
    return lines


def _render_design_refactor_block(refactor: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    approach = refactor.get("approach")
    if isinstance(approach, str) and approach.strip():
        lines.append(f"- approach: {approach.strip()}")
    preserve = refactor.get("preserve_public_api")
    if isinstance(preserve, bool):
        lines.append(f"- preserve_public_api: {'true' if preserve else 'false'}")
    policy = refactor.get("dead_code_policy")
    if isinstance(policy, str) and policy.strip():
        lines.append(f"- dead_code_policy: {policy.strip()}")
    hacks = refactor.get("allow_temporary_hacks")
    if isinstance(hacks, bool):
        lines.append(f"- allow_temporary_hacks: {'true' if hacks else 'false'}")
    return lines


def _render_design_acceptance_block(acceptance: Mapping[str, object]) -> list[str]:
    criteria = acceptance.get("criteria")
    if not isinstance(criteria, list):
        return []
    lines: list[str] = []
    for entry in criteria:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        desc = entry.get("description")
        if isinstance(cid, str) and isinstance(desc, str) and cid.strip() and desc.strip():
            lines.append(f"- {cid.strip()} — {desc.strip()}")
    return lines


def _render_verification_section(verification: object) -> list[str]:
    if not isinstance(verification, list) or not verification:
        return []

    lines = ["", "## Verification"]
    for check in verification:
        if not isinstance(check, dict):
            continue
        method = check.get("method")
        outcome = check.get("expected_outcome")
        if isinstance(method, str) and isinstance(outcome, str):
            lines.extend(["", f"- `{method}` — {outcome}"])
    return lines


def _render_parallel_plan_section(parallel_plan: object) -> list[str]:
    return _render_named_items_section(
        parallel_plan,
        heading="## Parallel Plan",
        id_key="id",
        description_key="description",
    )


def _render_work_units_section(work_units: object) -> list[str]:
    return _render_named_items_section(
        work_units,
        heading="## Work Units",
        id_key="unit_id",
        description_key="description",
    )


def _render_named_items_section(
    items: object,
    *,
    heading: str,
    id_key: str,
    description_key: str,
) -> list[str]:
    if not isinstance(items, list) or not items:
        return []

    lines = ["", heading]
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get(id_key)
        description = item.get(description_key)
        if isinstance(item_id, str) and isinstance(description, str):
            lines.extend(["", f"- **{item_id}** — {description}"])
    return lines


def write_plan_markdown(
    workspace_root: Path,
    content: Mapping[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Persist the agent-facing Markdown handoff for a normalized plan."""
    destination = workspace_root / ".agent" / "PLAN.md"
    backend.mkdir(destination.parent, parents=True, exist_ok=True)
    backend.write_text(destination, render_plan_markdown(content), encoding="utf-8")


__all__ = [
    "PLAN_MARKDOWN_PATH",
    "extract_plan_payload",
    "extract_plan_skill_names",
    "render_plan_markdown",
    "write_plan_markdown",
]
