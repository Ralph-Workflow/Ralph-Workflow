"""Hermetic scoring and opt-in execution seams for verification-prompt evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """Fixed criteria and optional planted defect locations for one evaluation case."""

    case_id: str
    criterion_ids: frozenset[str]
    defect_locations: frozenset[str]
    artifact_type: str = "development_analysis_decision"
    template_name: str = "development_analysis"

def render_evaluation_prompt(case: EvaluationCase) -> str:
    """Render the production verification template with fixed fixture payloads."""
    context = TemplateContext.default()
    template = context.registry.get_template(case.template_name)
    session = SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS)
    return render_template(
        template,
        {
            **capability_template_variables(
                session.capabilities, session.policy_flags, tool_name_prefix=session.tool_name_prefix
            ),
            "LAST_RETRY_ERROR": "",
            "PRODUCT_CRITERIA_PATH": "fixtures/request.md",
            "PLAN_PATH": "fixtures/plan.md",
            "gate_script_policy_path": "docs/ralph-workflow-policy/gate-script-policy.md",
            "approved_tools": "python",
            "submit_tool_names": "ralph_submit_md_artifact",
            "verify_tool_names": "ralph_verify_md_artifact",
            "declare_complete_tool_names": "declare_complete",
            "artifact_type": case.artifact_type,
        },
        context.partials,
    )


def run_evaluation(
    cases: Iterable[EvaluationCase],
    agents: Iterable[tuple[str, str]],
    invoke: Callable[[tuple[str, str], str, EvaluationCase], str],
) -> dict[str, dict[str, dict[str, float]]]:
    """Run supplied agents through production prompts and validate every submission.

    Callers explicitly opt in to live invocation; this seam keeps the default
    suite hermetic while ensuring evaluator output uses the production Markdown
    contract before scoring.
    """
    results: dict[str, dict[str, dict[str, float]]] = {}
    for agent in agents:
        name, _model = agent
        agent_results: dict[str, dict[str, float]] = {}
        for case in cases:
            content, diagnostics = parse_and_validate(
                invoke(agent, render_evaluation_prompt(case), case), get_spec(case.artifact_type)
            )
            if diagnostics:
                raise ValueError(f"invalid submitted decision for {name}/{case.case_id}")
            agent_results[case.case_id] = score_decisions(case, [content])
        results[name] = agent_results
    return results


def score_decisions(
    case: EvaluationCase,
    decisions: Iterable[Mapping[str, object]],
) -> dict[str, float]:
    """Score validated decision content without invoking an agent."""
    runs = tuple(decisions)
    if not runs:
        raise ValueError("at least one validated decision is required")
    verdict_sets = tuple(_verdicts(case, decision) for decision in runs)
    localized = {
        location
        for verdicts in verdict_sets
        for identifier, verdict, evidence, location in verdicts.values()
        if identifier in case.criterion_ids and verdict == "not met" and evidence and location
    }
    correct_case = not case.defect_locations
    false_rejections = (
        sum(any(verdict == "not met" for _, verdict, _, _ in run.values()) for run in verdict_sets)
        if correct_case
        else 0
    )
    met_count = sum(verdict == "met" for run in verdict_sets for _, verdict, _, _ in run.values())
    unsupported_met = sum(
        verdict == "met" and not evidence
        for run in verdict_sets
        for _, verdict, evidence, _ in run.values()
    )
    disagreement = sum(run != verdict_sets[0] for run in verdict_sets[1:])
    return {
        "localized_defect_recall": _ratio(len(localized & case.defect_locations), len(case.defect_locations)),
        "false_rejection_rate": _ratio(false_rejections, len(runs)),
        "unsupported_met_rate": _ratio(unsupported_met, met_count),
        "verdict_disagreement_rate": _ratio(disagreement, max(len(runs) - 1, 1)),
    }


def _verdicts(
    case: EvaluationCase, decision: Mapping[str, object]
) -> dict[str, tuple[str, str, bool, str]]:
    entries = decision.get("criterion_verdicts")
    identifiers = decision.get("criterion_verdict_ids")
    if not isinstance(entries, list) or not isinstance(identifiers, list):
        raise ValueError("decision must be production-validator content")
    raw_identifiers = cast("list[object]", identifiers)
    raw_entries = cast("list[object]", entries)
    if not all(isinstance(value, str) for value in (*raw_identifiers, *raw_entries)):
        raise ValueError("criterion verdict entries must be strings")
    parsed: dict[str, tuple[str, str, bool, str]] = {}
    for identifier, entry in zip(raw_identifiers, raw_entries, strict=True):
        identifier_text, entry_text = str(identifier), str(entry)
        verdict = entry_text.split("Verdict:", 1)[-1].split(".", 1)[0].strip().casefold()
        evidence = "Evidence:" in entry_text and bool(
            entry_text.split("Evidence:", 1)[1].split("Location:", 1)[0].strip()
        )
        location = entry_text.split("Location:", 1)[-1].strip().rstrip(".") if "Location:" in entry_text else ""
        parsed[identifier_text] = (identifier_text, verdict, evidence, location)
    return parsed


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


__all__ = [
    "EvaluationCase",
    "render_evaluation_prompt",
    "run_evaluation",
    "score_decisions",
]
