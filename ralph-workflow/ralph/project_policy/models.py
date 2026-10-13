"""Project-policy readiness models — re-export shim.

The canonical class definitions live in the per-attribute private modules:

* :mod:`ralph.project_policy._status` — :class:`ReadinessStatus`.
* :mod:`ralph.project_policy._findings` — :class:`PolicyFinding`.
* :mod:`ralph.project_policy._evidence` — :class:`EvidenceEntry`.
* :mod:`ralph.project_policy._migration` — :class:`MigrationCandidate`.
* :mod:`ralph.project_policy._command_outcome` — :class:`CommandOutcome`.
* :mod:`ralph.project_policy._readiness_assessment` — :class:`ReadinessAssessment`.
* :mod:`ralph.project_policy._assessment_error` — :class:`EvidenceParseError`.
* :mod:`ralph.project_policy._assessment` — the
  :func:`parse_execution_evidence` strict ingestion helper.
* :mod:`ralph.project_policy._result` — :class:`ReadinessResult`.

The classes are split per-file so each top-level class lives in its own
module (the repository structure policy caps each source file at one
public top-level class). This module preserves the original import path
``from ralph.project_policy.models import ReadinessStatus, PolicyFinding,
...`` for callers and tests that already depend on it.

No I/O. No AI. No network. All models are deterministic data carriers
that the validator, the cache, the orchestrator, the remediation driver,
and the display layer thread through a single shared contract.
"""

from __future__ import annotations

from ralph.project_policy._assessment import parse_execution_evidence
from ralph.project_policy._assessment_error import EvidenceParseError
from ralph.project_policy._command_outcome import CommandOutcome
from ralph.project_policy._evidence import EvidenceEntry
from ralph.project_policy._findings import PolicyFinding
from ralph.project_policy._migration import MigrationCandidate
from ralph.project_policy._readiness_assessment import ReadinessAssessment
from ralph.project_policy._result import ReadinessResult
from ralph.project_policy._status import ReadinessStatus


class PortfolioError(ValueError):
    """A deterministic, bounded portfolio-contract validation failure."""


__all__ = [
    "CommandOutcome",
    "EvidenceEntry",
    "EvidenceParseError",
    "MigrationCandidate",
    "PolicyFinding",
    "PortfolioError",
    "ReadinessAssessment",
    "ReadinessResult",
    "ReadinessStatus",
    "parse_execution_evidence",
]
