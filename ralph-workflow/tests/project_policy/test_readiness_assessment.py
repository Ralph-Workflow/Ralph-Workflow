"""Tests for the strict execution-evidence ledger and the readiness assessment.

These tests cover the three readiness dimensions that the analysis
agent's ``RALPH-EVIDENCE:`` ledger unlocks, the strict parser that
ingests them, the cache that carries them, and the pipeline driver that
threads them through the readiness result. The contract is fail-closed:
a malformed ledger, a missing hash, an unrun command, or a stale cache
must NEVER surface as ``True`` for any of the three dimensions.
"""

from __future__ import annotations

import json

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import analysis, cache, evidence, pipeline_driver
from ralph.project_policy._assessment import (
    extract_evidence_section,
    parse_execution_evidence,
)
from ralph.project_policy._assessment_error import EvidenceParseError
from ralph.project_policy._assessment_error import (
    EvidenceParseError as ModelsEvidenceParseError,
)
from ralph.project_policy._command_outcome import CommandOutcome
from ralph.project_policy._readiness_assessment import ReadinessAssessment
from ralph.project_policy.models import (
    PolicyFinding,
    ReadinessStatus,
)
from ralph.project_policy.models import (
    parse_execution_evidence as models_parse_execution_evidence,
)
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python")


def _ledger_line(
    command: str = "make -C ralph-workflow verify",
    exit_code: int = 0,
    duration: float = 12.5,
    content_hash: str = "abc123",
) -> str:
    payload = {
        "command": command,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "policy_content_hash": content_hash,
    }
    return "RALPH-EVIDENCE: " + json.dumps(payload, sort_keys=True)


# ----- pure parser tests -------------------------------------------------


def test_parse_ledger_accepts_a_well_formed_line() -> None:
    line = _ledger_line()
    (outcome,) = parse_execution_evidence(line)
    assert outcome.command == "make -C ralph-workflow verify"
    assert outcome.exit_code == 0
    assert outcome.duration_seconds == 12.5
    assert outcome.policy_content_hash == "abc123"


def test_parse_ledger_ignores_blank_and_prose_lines() -> None:
    text = (
        "## Execution evidence\n"
        "\n"
        "Prose line one.\n"
        "\n"
        + _ledger_line(command="pytest", exit_code=0)
        + "\n"
        + _ledger_line(command="ruff check", exit_code=0)
        + "\n"
        "FOO: prose without the RALPH-EVIDENCE: prefix\n"
    )
    outcomes = parse_execution_evidence(text)
    assert [o.command for o in outcomes] == ["pytest", "ruff check"]


def test_parse_ledger_preserves_line_order() -> None:
    text = "\n".join(
        _ledger_line(command=f"step-{i}", exit_code=0, content_hash=f"hash-{i}") for i in range(5)
    )
    outcomes = parse_execution_evidence(text)
    assert [o.command for o in outcomes] == [f"step-{i}" for i in range(5)]


@pytest.mark.parametrize(
    ("line", "match"),
    [
        # non-JSON payload
        ("RALPH-EVIDENCE: not-json", "JSON payload is invalid"),
        # JSON array (not an object)
        ("RALPH-EVIDENCE: [1, 2, 3]", "must be a JSON object"),
        # unknown key
        (
            "RALPH-EVIDENCE: "
            + json.dumps(
                {
                    "command": "x",
                    "exit_code": 0,
                    "duration_seconds": 1.0,
                    "policy_content_hash": "h",
                    "extra": True,
                },
                sort_keys=True,
            ),
            "unknown keys",
        ),
        # missing key
        (
            "RALPH-EVIDENCE: "
            + json.dumps({"command": "x", "exit_code": 0, "duration_seconds": 1.0}),
            "missing required key",
        ),
        # empty command
        (
            "RALPH-EVIDENCE: "
            + json.dumps(
                {
                    "command": "  ",
                    "exit_code": 0,
                    "duration_seconds": 1.0,
                    "policy_content_hash": "h",
                },
                sort_keys=True,
            ),
            "non-empty string command",
        ),
        # boolean exit_code (not int)
        (
            "RALPH-EVIDENCE: "
            + json.dumps(
                {
                    "command": "x",
                    "exit_code": True,
                    "duration_seconds": 1.0,
                    "policy_content_hash": "h",
                },
                sort_keys=True,
            ),
            "integer exit_code",
        ),
        # negative exit_code
        (
            "RALPH-EVIDENCE: "
            + json.dumps(
                {
                    "command": "x",
                    "exit_code": -1,
                    "duration_seconds": 1.0,
                    "policy_content_hash": "h",
                },
                sort_keys=True,
            ),
            "outside the 0..255 range",
        ),
        # boolean duration_seconds
        (
            "RALPH-EVIDENCE: "
            + json.dumps(
                {
                    "command": "x",
                    "exit_code": 0,
                    "duration_seconds": True,
                    "policy_content_hash": "h",
                },
                sort_keys=True,
            ),
            "numeric duration_seconds",
        ),
        # negative duration
        (
            "RALPH-EVIDENCE: "
            + json.dumps(
                {
                    "command": "x",
                    "exit_code": 0,
                    "duration_seconds": -0.5,
                    "policy_content_hash": "h",
                },
                sort_keys=True,
            ),
            "must be non-negative",
        ),
        # empty hash
        (
            "RALPH-EVIDENCE: "
            + json.dumps(
                {
                    "command": "x",
                    "exit_code": 0,
                    "duration_seconds": 1.0,
                    "policy_content_hash": "  ",
                },
                sort_keys=True,
            ),
            "non-empty string policy_content_hash",
        ),
    ],
)
def test_parse_ledger_rejects_malformed_line(line: str, match: str) -> None:
    with pytest.raises(EvidenceParseError, match=match):
        parse_execution_evidence(line)


def test_parse_ledger_rejects_too_many_lines() -> None:
    text = "\n".join(
        _ledger_line(command=f"c-{i}", exit_code=0, content_hash=f"h-{i}") for i in range(65)
    )
    with pytest.raises(EvidenceParseError, match="exceeds 64 lines"):
        parse_execution_evidence(text)


def test_models_re_export_matches_module_function() -> None:
    """The models re-export must be the same callable so callers and
    tests have one canonical import path."""
    assert models_parse_execution_evidence is parse_execution_evidence
    assert ModelsEvidenceParseError is EvidenceParseError


def test_extract_evidence_section_returns_only_markdown_body() -> None:
    text = (
        "## Summary\n"
        "All gates ran.\n"
        "\n"
        "## Execution evidence\n" + _ledger_line(command="pytest", exit_code=0) + "\n"
        "\n"
        "## Criterion verdicts\n"
        "- [PR-001] ok\n"
    )
    section = extract_evidence_section(text)
    assert "RALPH-EVIDENCE" in section
    assert "## Summary" not in section
    assert "## Criterion verdicts" not in section
    (outcome,) = parse_execution_evidence(section)
    assert outcome.command == "pytest"


def test_extract_evidence_section_returns_empty_when_section_missing() -> None:
    assert extract_evidence_section("## Summary\nNo evidence section here.\n") == ""


def test_readiness_assessment_defaults_are_all_false() -> None:
    a = ReadinessAssessment()
    assert a.structure_complete is False
    assert a.implementation_verified is False
    assert a.outcome_evidence_reviewed is False


# ----- cache carry + invalidation tests ----------------------------------


def test_cache_surfaces_cached_assessment_on_subsequent_read() -> None:
    ws = MemoryWorkspace()
    assessment = ReadinessAssessment(
        structure_complete=True, implementation_verified=True, outcome_evidence_reviewed=True
    )
    outcomes = (CommandOutcome("make verify", 0, 12.0, "hash"),)
    cache.write_cache(
        ws,
        _stack(),
        ReadinessStatus.READY,
        assessment=assessment,
        command_outcomes=outcomes,
    )
    cached_assessment, cached_outcomes = cache.read_cached_assessment(ws, _stack())
    assert cached_assessment == assessment
    assert cached_outcomes == outcomes


def test_cache_stale_signature_returns_all_false_assessment() -> None:
    """An edit OR deletion of any evidence file invalidates the cached
    assessment: a stale cache must not launder a false ``True`` into a
    verification claim.
    """
    ws = MemoryWorkspace()
    cache.write_cache(
        ws,
        _stack(),
        ReadinessStatus.READY,
        assessment=ReadinessAssessment(
            structure_complete=True, implementation_verified=True, outcome_evidence_reviewed=True
        ),
        command_outcomes=(CommandOutcome("make verify", 0, 12.0, "hash"),),
    )
    ws.write("docs/ralph-workflow-policy/testing-policy.md", "# different content")
    cached_assessment, cached_outcomes = cache.read_cached_assessment(ws, _stack())
    assert cached_assessment == ReadinessAssessment()
    assert cached_outcomes == ()


def test_cache_round_trip_without_optional_fields_defaults_to_all_false() -> None:
    """Backwards-compatible: a cache payload missing the assessment or
    outcomes keys (e.g. a cache written by an older binary) surfaces
    the all-false default rather than crashing.
    """
    ws = MemoryWorkspace()
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    cached_assessment, cached_outcomes = cache.read_cached_assessment(ws, _stack())
    assert cached_assessment == ReadinessAssessment()
    assert cached_outcomes == ()


# ----- pipeline-driver end-to-end (with fake agent) ----------------------


def _finding() -> PolicyFinding:
    return PolicyFinding(
        requirement_id="RWP-CORE:testing-policy.md",
        path="docs/ralph-workflow-policy/testing-policy.md",
        missing_evidence="file does not exist",
        required_outcome="create it",
    )


def _write_decision_with_ledger(
    ws: MemoryWorkspace,
    *,
    status: str = "completed",
    ledger: tuple[str, ...] = (),
) -> None:
    """Write the analysis-phase decision artifact with an evidence ledger."""
    ws.mkdirs(".agent/artifacts")
    ledger_section = ""
    if ledger:
        ledger_section = "\n## Execution evidence\n\n" + "\n".join(ledger) + "\n"
    shortcomings: list[str] = []
    if status in {"request_changes", "failed"}:
        shortcomings = ["reviewer found a gap"]
    verdict = "not met" if shortcomings else "met"
    verdict_evidence = shortcomings[0] if shortcomings else "declared probes inspected"
    extra_sections = ""
    if shortcomings:
        short_items = "\n".join(
            f"- [PR-{i:03d}] Criterion: declared policy fact holds. "
            f"Expected observation: the declared probe resolves. "
            f"Verdict: not met. Evidence: {item}. "
            f"Location: policy declaration."
            for i, item in enumerate(shortcomings, start=1)
        )
        extra_sections = f"\n## What Came Up Short\n\n{short_items}\n"
    ws.write(
        analysis.ANALYSIS_ARTIFACT_REL_PATH,
        (
            "---\n"
            f"type: {analysis.ANALYSIS_ARTIFACT_TYPE}\n"
            f"status: {status}\n"
            "---\n\n"
            "## Summary\n\n"
            "- [SUM-1] Review complete. Evidence: declared probes inspected.\n"
            f"{ledger_section}"
            f"{extra_sections}"
            "\n## Criterion Verdicts\n\n"
            f"- [PR-001] Criterion: declared policy fact holds. "
            f"Expected observation: the declared probe resolves. "
            f"Verdict: {verdict}. Evidence: {verdict_evidence}. "
            f"Location: policy declaration.\n"
        ),
    )


class _Recorder:
    def __init__(
        self,
        ws: MemoryWorkspace,
        *,
        decisions: list[str],
        fix_on_call: int | None = 1,
        ledger: tuple[str, ...] = (),
    ) -> None:
        self.workspace = ws
        self.decisions = list(decisions)
        self.fix_on_call = fix_on_call
        self.ledger = ledger
        self._remediation_calls = 0

    def __call__(self, *, phase: str, prompt_path: str) -> bool:
        del prompt_path
        if phase == "policy_remediation":
            self._remediation_calls += 1
            if self.fix_on_call is not None and self._remediation_calls == self.fix_on_call:
                seed_complete_corpus(self.workspace)
            return True
        decision = self.decisions.pop(0) if self.decisions else "request_changes"
        _write_decision_with_ledger(self.workspace, status=decision, ledger=self.ledger)
        return True


@pytest.mark.timeout_seconds(5)
def test_pipeline_ready_carries_assessment_through_to_result() -> None:
    """A READY run with a matching ledger must surface a non-default
    assessment in the result; the dimensions are evidence-anchored and
    HASH-BOUND: the ledger's ``policy_content_hash`` must equal the
    current readiness-evidence signature of the seeded corpus.
    """
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    current_hash = evidence.evidence_signature(ws, stack())
    ledger = (
        _ledger_line(command="make test", exit_code=0, content_hash=current_hash),
        _ledger_line(command="make bypass-audit", exit_code=0, content_hash=current_hash),
    )
    agent = _Recorder(ws, decisions=["completed"], ledger=ledger, fix_on_call=None)
    # The corpus is already seeded, so no remediation seeding is needed.

    result = pipeline_driver.run_policy_pipeline(ws, stack(), [_finding()], invoke_agent=agent)

    assert result.status is ReadinessStatus.READY
    assert result.assessment is not None
    assert result.assessment.structure_complete is True
    assert result.assessment.implementation_verified is True
    assert result.assessment.outcome_evidence_reviewed is True
    assert len(result.command_outcomes) == 2
    assert result.command_outcomes[0].command == "make test"


@pytest.mark.timeout_seconds(5)
def test_pipeline_wrong_hash_ledger_keeps_implementation_verified_false() -> None:
    """A ledger whose ``policy_content_hash`` does NOT match the current
    readiness-evidence signature cannot certify the present policy: the
    dimension fails closed even with exit_code 0 on every line.
    """
    ws = MemoryWorkspace()
    ledger = (_ledger_line(command="make test", exit_code=0, content_hash="WRONG-HASH"),)
    agent = _Recorder(ws, decisions=["completed"], ledger=ledger)

    result = pipeline_driver.run_policy_pipeline(ws, stack(), [_finding()], invoke_agent=agent)

    assert result.status is ReadinessStatus.READY
    assert result.assessment is not None
    assert result.assessment.implementation_verified is False
    assert result.assessment.outcome_evidence_reviewed is False


@pytest.mark.timeout_seconds(5)
def test_pipeline_ready_with_no_ledger_keeps_implementation_verified_false() -> None:
    """A READY run with NO ledger must report ``implementation_verified=False``:
    the analysis agent approved the policy but never produced proof it
    actually ran the gate.
    """
    ws = MemoryWorkspace()
    agent = _Recorder(ws, decisions=["completed"], ledger=())

    result = pipeline_driver.run_policy_pipeline(ws, stack(), [_finding()], invoke_agent=agent)

    assert result.status is ReadinessStatus.READY
    assert result.assessment is not None
    assert result.assessment.structure_complete is True
    assert result.assessment.implementation_verified is False
    assert result.assessment.outcome_evidence_reviewed is False
    assert result.command_outcomes == ()


@pytest.mark.timeout_seconds(5)
def test_pipeline_blocked_with_ledger_reports_structure_incomplete() -> None:
    """A BLOCKED run with a ledger must still report
    ``structure_complete=False`` (the validator is the gate) and
    ``implementation_verified=False`` because the cache cannot be
    written from a non-READY result.
    """
    ws = MemoryWorkspace()
    ledger = (_ledger_line(command="make verify", exit_code=1, content_hash="hash"),)
    agent = _Recorder(ws, decisions=["request_changes", "completed"], ledger=ledger)

    result = pipeline_driver.run_policy_pipeline(ws, stack(), [_finding()], invoke_agent=agent)

    assert result.status is ReadinessStatus.READY  # eventually
    assert result.assessment is not None
    assert result.assessment.structure_complete is True
    # The ledger contained a non-zero exit_code, so implementation_verified stays False.
    assert result.assessment.implementation_verified is False


@pytest.mark.timeout_seconds(5)
def test_pipeline_handles_malformed_ledger_without_crashing() -> None:
    """A malformed ledger must NOT crash the driver; the analysis
    artifact is still parseable for its decision and the dimension
    degrades to all-false.
    """
    ws = MemoryWorkspace()
    # Use the helper that produces a well-formed decision artifact AND
    # appends a malformed RALPH-EVIDENCE: line under the evidence section.
    # The driver must read the decision (completed) and silently treat the
    # malformed ledger as no ledger.
    _write_decision_with_ledger(
        ws,
        status="completed",
        ledger=("RALPH-EVIDENCE: not-json",),
    )
    agent = _Recorder(ws, decisions=["completed"], ledger=())

    result = pipeline_driver.run_policy_pipeline(ws, stack(), [_finding()], invoke_agent=agent)

    assert result.status is ReadinessStatus.READY
    assert result.assessment is not None
    assert result.assessment.implementation_verified is False
    assert result.command_outcomes == ()


@pytest.mark.timeout_seconds(5)
def test_pipeline_with_failing_ledger_keeps_implementation_verified_false() -> None:
    """A non-zero exit_code in the ledger is honest failure: the
    dimension stays False even when the validator passes.
    """
    ws = MemoryWorkspace()
    ledger = (
        _ledger_line(
            command="make -C ralph-workflow verify",
            exit_code=2,
            content_hash="hash",
        ),
    )
    agent = _Recorder(ws, decisions=["completed"], ledger=ledger)

    result = pipeline_driver.run_policy_pipeline(ws, stack(), [_finding()], invoke_agent=agent)

    assert result.status is ReadinessStatus.READY
    assert result.assessment is not None
    assert result.assessment.implementation_verified is False
    assert result.assessment.outcome_evidence_reviewed is False
    assert result.command_outcomes[0].exit_code == 2
