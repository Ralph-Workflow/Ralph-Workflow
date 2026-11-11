
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.project_policy import evidence, markers
from ralph.project_policy._assessment import parse_execution_evidence
from ralph.project_policy._assessment_error import EvidenceParseError
from ralph.project_policy._readiness_assessment import ReadinessAssessment
from ralph.project_policy.models import ReadinessStatus

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy._command_outcome import CommandOutcome
    from ralph.workspace.protocol import Workspace

_STATUS_KEY = "status"
_SIGNATURE_KEY = "signature"
_ASSESSMENT_KEY = "assessment"
_OUTCOMES_KEY = "outcomes"
_DEFAULT_ASSESSMENT = ReadinessAssessment()


def _default_assessment_dict() -> dict[str, bool]:
    return _DEFAULT_ASSESSMENT.as_dict()


def _serialize_outcomes(outcomes: tuple[CommandOutcome, ...]) -> list[dict[str, object]]:
    return [
        {
            "command": outcome.command,
            "exit_code": outcome.exit_code,
            "duration_seconds": outcome.duration_seconds,
            "policy_content_hash": outcome.policy_content_hash,
        }
        for outcome in outcomes
    ]


def _read_cache(workspace: Workspace) -> dict[str, object] | None:
    if not workspace.exists(markers.CACHE_REL_PATH):
        return None
    try:
        raw = workspace.read(markers.CACHE_REL_PATH)
    except FileNotFoundError:
        return None
    parsed: object
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _assessment_from_cache(payload: dict[str, object]) -> ReadinessAssessment:
    raw = payload.get(_ASSESSMENT_KEY)
    if not isinstance(raw, dict):
        return _DEFAULT_ASSESSMENT
    if not all(key in raw for key in _default_assessment_dict()):
        return _DEFAULT_ASSESSMENT
    values: list[bool] = []
    for key in _default_assessment_dict():
        value = raw[key]
        if not isinstance(value, bool):
            return _DEFAULT_ASSESSMENT
        values.append(value)
    return ReadinessAssessment(*values)


def _outcomes_from_cache(payload: dict[str, object]) -> tuple[CommandOutcome, ...]:
    raw = payload.get(_OUTCOMES_KEY)
    if not isinstance(raw, list):
        return ()
    try:
        serialized: str = json.dumps(raw, sort_keys=True)
        decoded: object = json.loads(serialized)
        if not isinstance(decoded, list):
            return ()
        return parse_execution_evidence(
            "\n".join("RALPH-EVIDENCE: " + json.dumps(item, sort_keys=True) for item in decoded)
        )
    except (EvidenceParseError, json.JSONDecodeError, TypeError):
        return ()


def read_cached_ready(workspace: Workspace, stack: ProjectStack) -> bool:
    payload = _read_cache(workspace)
    if payload is None:
        return False
    if payload.get(_STATUS_KEY) != ReadinessStatus.READY.value:
        return False
    cached_signature = payload.get(_SIGNATURE_KEY)
    if not isinstance(cached_signature, str):
        return False
    current_signature = evidence.evidence_signature(workspace, stack)
    return cached_signature == current_signature


def read_cached_assessment(
    workspace: Workspace, stack: ProjectStack
) -> tuple[ReadinessAssessment, tuple[CommandOutcome, ...]]:
    if not read_cached_ready(workspace, stack):
        return _DEFAULT_ASSESSMENT, ()
    payload = _read_cache(workspace)
    if payload is None:
        return _DEFAULT_ASSESSMENT, ()
    return _assessment_from_cache(payload), _outcomes_from_cache(payload)


def write_cache(
    workspace: Workspace,
    stack: ProjectStack,
    status: ReadinessStatus,
    *,
    assessment: ReadinessAssessment | None = None,
    command_outcomes: tuple[CommandOutcome, ...] = (),
) -> None:
    if status is not ReadinessStatus.READY:
        return
    payload: dict[str, object] = {
        _STATUS_KEY: status.value,
        _SIGNATURE_KEY: evidence.evidence_signature(workspace, stack),
        _ASSESSMENT_KEY: (assessment or _DEFAULT_ASSESSMENT).as_dict(),
        _OUTCOMES_KEY: _serialize_outcomes(command_outcomes),
    }
    cache_path = markers.CACHE_REL_PATH
    parent_dir = "/".join(cache_path.split("/")[:-1])
    if parent_dir:
        workspace.mkdirs(parent_dir)
    workspace.write(cache_path, json.dumps(payload, sort_keys=True))


__all__ = [
    "read_cached_assessment",
    "read_cached_ready",
    "write_cache",
]
