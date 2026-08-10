"""Hermetic scoring and opt-in execution seams for verification-prompt evaluations."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping


_PHASES = {
    "planning_analysis": "planning_analysis_decision",
    "development_analysis": "development_analysis_decision",
    "policy_remediation_analysis": "policy_remediation_analysis_decision",
}
_CASE_KEYS = frozenset(
    {
        "case_id",
        "artifact_type",
        "template_name",
        "criterion_ids",
        "defect_locations",
        "request",
        "plan_or_policy",
        "workspace_files",
        "expected_artifact_type",
    }
)


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One immutable verification fixture and its measurable expected outcomes."""

    case_id: str
    criterion_ids: frozenset[str]
    defect_locations: frozenset[str]
    artifact_type: str = "development_analysis_decision"
    template_name: str = "development_analysis"
    request: str = ""
    plan_or_policy: str = ""
    workspace_files: Mapping[str, str] | None = None


def load_evaluation_cases(path: Path) -> tuple[EvaluationCase, ...]:
    """Load and validate frozen evaluation cases without accepting ambient workspace data."""
    try:
        raw_cases: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load evaluation cases from {path}") from exc
    if not isinstance(raw_cases, list):
        raise ValueError("evaluation cases must be a JSON array")

    cases: list[EvaluationCase] = []
    case_ids: set[str] = set()
    for raw_case in raw_cases:
        case = _parse_case(raw_case)
        if case.case_id in case_ids:
            raise ValueError(f"duplicate evaluation case ID: {case.case_id}")
        case_ids.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def _parse_case(raw_case: object) -> EvaluationCase:
    if not isinstance(raw_case, dict) or not all(isinstance(key, str) for key in raw_case):
        raise ValueError("each evaluation case must have the complete supported shape")
    values: dict[str, object] = dict(raw_case)
    if set(values) != _CASE_KEYS:
        raise ValueError("each evaluation case must have the complete supported shape")
    case_id = values["case_id"]
    artifact_type = values["artifact_type"]
    template_name = values["template_name"]
    expected_artifact_type = values["expected_artifact_type"]
    request = values["request"]
    plan_or_policy = values["plan_or_policy"]
    criterion_ids = values["criterion_ids"]
    defect_locations = values["defect_locations"]
    workspace_files = values["workspace_files"]
    if (
        not isinstance(case_id, str)
        or not isinstance(artifact_type, str)
        or not isinstance(template_name, str)
        or not isinstance(expected_artifact_type, str)
        or not isinstance(request, str)
        or not isinstance(plan_or_policy, str)
    ):
        raise ValueError("evaluation case text fields must be strings")
    if not case_id or not _safe_relative_path(case_id):
        raise ValueError("evaluation case ID must be a safe non-empty relative name")
    if _PHASES.get(template_name) != artifact_type or expected_artifact_type != artifact_type:
        raise ValueError("evaluation case must map one supported template to its production artifact type")
    if not isinstance(criterion_ids, list) or not criterion_ids or not all(
        isinstance(item, str) and item for item in criterion_ids
    ):
        raise ValueError("evaluation case criterion_ids must be a non-empty string list")
    if not isinstance(defect_locations, list) or not all(isinstance(item, str) for item in defect_locations):
        raise ValueError("evaluation case defect_locations must be a string list")
    safe_workspace_files = _parse_workspace_files(workspace_files)
    return EvaluationCase(
        case_id=case_id,
        criterion_ids=frozenset(criterion_ids),
        defect_locations=frozenset(defect_locations),
        artifact_type=artifact_type,
        template_name=template_name,
        request=request,
        plan_or_policy=plan_or_policy,
        workspace_files=safe_workspace_files,
    )


def _parse_workspace_files(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("evaluation case workspace_files must map safe relative paths to strings")
    parsed: dict[str, str] = {}
    for path, content in value.items():
        if not isinstance(path, str) or not isinstance(content, str) or not _safe_relative_path(path):
            raise ValueError("evaluation case workspace_files must map safe relative paths to strings")
        parsed[path] = content
    return parsed


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and value not in {"", "."}


def render_evaluation_prompt(case: EvaluationCase, workspace_path: Path | None = None) -> str:
    """Render the mapped production verifier with only frozen fixture paths."""
    context = TemplateContext.default()
    template = context.registry.get_template(case.template_name)
    session = SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS)
    root = Path("fixtures") if workspace_path is None else workspace_path
    return render_template(
        template,
        {
            **capability_template_variables(
                session.capabilities, session.policy_flags, tool_name_prefix=session.tool_name_prefix
            ),
            "LAST_RETRY_ERROR": "",
            "HAS_DOCS_MCP": "",
            "DOCS_MCP_PORT": "localhost:6280",
            "DOCS_LOOKUP_PHASE": "analysis",
            "DOCS_LOOKUP_VARIANT": "",
            "PRODUCT_CRITERIA_PATH": str(root / "request.md"),
            "PLAN_PATH": str(root / "plan-or-policy.md"),
            "gate_script_policy_path": str(root / "plan-or-policy.md"),
            "approved_tools": "python",
            "submit_tool_names": "ralph_submit_md_artifact",
            "verify_tool_names": "ralph_verify_md_artifact",
            "declare_complete_tool_names": "declare_complete",
            "artifact_type": case.artifact_type,
        },
        context.partials,
    )


def _populate_workspace(root: Path, case: EvaluationCase) -> None:
    (root / "request.md").write_text(case.request, encoding="utf-8")
    (root / "plan-or-policy.md").write_text(case.plan_or_policy, encoding="utf-8")
    for relative_path, content in (case.workspace_files or {}).items():
        if not _safe_relative_path(relative_path):
            raise ValueError(f"unsafe fixture path: {relative_path}")
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def run_evaluation(
    cases: Iterable[EvaluationCase],
    agents: Iterable[tuple[str, str]],
    invoke: Callable[[tuple[str, str], str, Path, EvaluationCase], str],
    *,
    runs_per_agent: int = 1,
) -> dict[str, dict[str, dict[str, float]]]:
    """Run opt-in callbacks in isolated fixture workspaces and score their validated decisions."""
    if runs_per_agent < 1:
        raise ValueError("runs_per_agent must be positive")
    results: dict[str, dict[str, dict[str, float]]] = {}
    for agent in agents:
        name, _model = agent
        agent_results: dict[str, dict[str, float]] = {}
        for case in cases:
            decisions: list[dict[str, object]] = []
            for _ in range(runs_per_agent):
                with tempfile.TemporaryDirectory(prefix="ralph-verification-evaluation-") as temporary_directory:
                    workspace_path = Path(temporary_directory)
                    _populate_workspace(workspace_path, case)
                    prompt = render_evaluation_prompt(case, workspace_path)
                    content, diagnostics = parse_and_validate(
                        invoke(agent, prompt, workspace_path, case), get_spec(case.artifact_type)
                    )
                if diagnostics:
                    raise ValueError(f"invalid submitted decision for {name}/{case.case_id}")
                decisions.append(content)
            agent_results[case.case_id] = score_decisions(case, decisions)
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
    "load_evaluation_cases",
    "render_evaluation_prompt",
    "run_evaluation",
    "score_decisions",
]
