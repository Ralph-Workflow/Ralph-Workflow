"""Change-aware READY cache for the project-policy readiness preflight.

The cache is the FAST PATH for the orchestrator: when the same project
state was already validated READY on a prior preflight, the validator is
skipped on the current preflight. The cache hashes the
:func:`ralph.project_policy.evidence.evidence_signature` of the project —
the same signature the validator iterates — so any edit OR deletion of
any evidence file invalidates a cached READY automatically.

The cache also stores the readiness-assessment dimensions so a
subsequent run can surface them without re-running the analysis drain.
The signature key is unchanged; the dimensions are an additive payload
and the cache refuses to surface any of them as verified when the
signature does not match (the dimensions are part of the cached
contract, not a hint).

The cache is stored at :data:`markers.CACHE_REL_PATH` (relative to the
workspace root) so it travels with the workspace but stays out of source
control. The cache only ever stores a READY status — remediation-required
and blocked states are never cached because they require an agent run.
"""

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
    """Read the cache file via the workspace seam; return None when missing."""
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
    """Return the cached assessment, or all-false when the payload is malformed.

    A cache that fails the contract must NEVER launder a false ``True``
    into a verification claim; the conservative default is "all-false",
    which downstream consumers treat as "not verified".
    """
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
    """Return the cached outcomes, or an empty tuple when the payload is malformed.

    Outcomes travel through the cache as a strict JSON list. Any
    deviation (wrong type, missing keys, non-string command) yields an
    empty tuple rather than a runtime crash.
    """
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
    """Return True only when the cache holds a matching READY signature.

    A cached READY whose signature does NOT match the current
    :func:`ralph.project_policy.evidence.evidence_signature` is treated as
    stale: any edit OR deletion of an evidence file changes the signature,
    so a stale cache can never return a false READY.
    """
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
    """Return the cached assessment + outcomes for a matching READY signature.

    A stale or missing cache returns all-false + empty outcomes; this is
    the same fail-closed default the assessment dataclass uses.
    """
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
    """Persist the current evidence signature, status, and assessment to the cache file.

    The cached assessment is advisory; the cache write does NOT require
    a non-default assessment. A run that completed READY without
    surfacing any outcomes still writes an all-false assessment so a
    subsequent preflight sees the same dimensions it would have seen
    from a re-derivation.
    """
    if status is not ReadinessStatus.READY:
        # We only cache READY. Any other status would be misleading.
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
