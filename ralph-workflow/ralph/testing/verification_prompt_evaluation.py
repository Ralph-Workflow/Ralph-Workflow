"""Hermetic scoring for opt-in verification-prompt evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """Fixed criteria and optional planted defect locations for one evaluation case."""

    case_id: str
    criterion_ids: frozenset[str]
    defect_locations: frozenset[str]


def score_decisions(
    case: EvaluationCase,
    decisions: Iterable[Mapping[str, object]],
) -> dict[str, float]:
    """Score validated decision content without invoking an agent.

    Callers run real agents only in an explicit opt-in profile, parse their
    artifacts through the production Markdown validator, and pass the resulting
    content mappings here.
    """
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
    typed_identifiers = tuple(str(value) for value in raw_identifiers)
    typed_entries = tuple(str(value) for value in raw_entries)
    parsed: dict[str, tuple[str, str, bool, str]] = {}
    for identifier, entry in zip(typed_identifiers, typed_entries, strict=True):
        verdict = entry.split("Verdict:", 1)[-1].split(".", 1)[0].strip().casefold()
        evidence = "Evidence:" in entry and bool(entry.split("Evidence:", 1)[1].split("Location:", 1)[0].strip())
        location = entry.split("Location:", 1)[-1].strip().rstrip(".") if "Location:" in entry else ""
        parsed[identifier] = (identifier, verdict, evidence, location)
    return parsed


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


__all__ = ["EvaluationCase", "score_decisions"]
