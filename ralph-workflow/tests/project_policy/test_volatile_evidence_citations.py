from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.project_policy import markers, remediation, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack

if TYPE_CHECKING:
    from ralph.project_policy.models import PolicyFinding

_VERIFICATION = "verification-policy.md"
_TESTING = "testing-policy.md"


def _ready_workspace() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    return ws


def _append(ws: MemoryWorkspace, filename: str, text: str) -> None:
    path = f"{markers.CANONICAL_DIR}{filename}"
    ws.write(path, ws.read(path) + "\n" + text + "\n")


def _volatile(ws: MemoryWorkspace) -> list[PolicyFinding]:
    return [
        f
        for f in validators.validate_readiness(ws, stack())
        if f.requirement_id.startswith(markers.ID_VOLATILE_EVIDENCE)
    ]


def test_the_complete_corpus_cites_no_volatile_evidence() -> None:
    assert _volatile(_ready_workspace()) == []


@pytest.mark.parametrize(
    "citation",
    [
        "tmp/verify-stages.ndjson:23261",
        "tmp/verify-last-run.wt02.json:7",
        ".agent/raw/opencode.log:120",
        "logs/gate.log:44",
        "build/ledger.ndjson:9",
        "artifacts/run.jsonl:3",
    ],
)
@pytest.mark.parametrize("filename", [_VERIFICATION, _TESTING])
def test_a_line_citation_into_worktree_local_evidence_blocks_readiness(
    citation: str, filename: str
) -> None:
    ws = _ready_workspace()
    _append(ws, filename, f"The profile last passed ({citation}).")

    findings = _volatile(ws)

    assert [f.requirement_id for f in findings] == [f"{markers.ID_VOLATILE_EVIDENCE}:{filename}"]
    assert findings[0].blocking is True
    assert citation in findings[0].required_outcome
    assert "replace" in findings[0].required_outcome
    assert "re-point" in findings[0].required_outcome


@pytest.mark.parametrize(
    "text",
    [
        "Code lives in src/app/models/user.rb:42 and docs/guide.md:10.",
        "Per-run logs go to `tmp/` and are never committed.",
        "The profile last ran on 2026-11-20 (CI run 48213).",
        "Timeout lives in ralph/verify.py:_VERIFY_STEP_TIMEOUT_SECONDS.",
    ],
)
def test_durable_references_do_not_block_readiness(text: str) -> None:
    ws = _ready_workspace()
    _append(ws, _VERIFICATION, text)

    assert _volatile(ws) == []


def test_many_citations_produce_one_finding_that_counts_them() -> None:
    ws = _ready_workspace()
    _append(
        ws, _VERIFICATION, "\n".join(f"row tmp/verify-stages.ndjson:{n}" for n in range(100, 140))
    )

    findings = _volatile(ws)

    assert len(findings) == 1
    assert "40 citation" in findings[0].missing_evidence


def test_re_pointing_a_citation_is_not_remediation_progress() -> None:
    before_ws = _ready_workspace()
    _append(before_ws, _VERIFICATION, "Last pass: tmp/verify-stages.ndjson:22969.")
    after_ws = _ready_workspace()
    _append(after_ws, _VERIFICATION, "Last pass: tmp/verify-stages.ndjson:23262.")

    assert remediation.made_progress(_volatile(before_ws), _volatile(after_ws)) is False


def test_removing_a_citation_is_remediation_progress() -> None:
    before_ws = _ready_workspace()
    _append(
        before_ws, _VERIFICATION, "A tmp/verify-stages.ndjson:1 and tmp/verify-stages.ndjson:2."
    )
    after_ws = _ready_workspace()
    _append(after_ws, _VERIFICATION, "A tmp/verify-stages.ndjson:1 only.")

    assert remediation.made_progress(_volatile(before_ws), _volatile(after_ws)) is True


@pytest.mark.parametrize(
    "citation",
    [
        "`tmp/verify-stages.ndjson`:23261",
        "tmp/verify-stages.ndjson: 23261",
        "tmp/verify-stages.ndjson#L23261",
        "receipt tmp/verification-logs/standalone-20261125T030405Z-828242.log",
        "receipt .agent/raw/opencode_minimax.overflow.log",
    ],
)
def test_common_spellings_of_volatile_evidence_are_findings(citation: str) -> None:
    ws = _ready_workspace()
    _append(ws, _VERIFICATION, f"Evidence: {citation}.")

    assert len(_volatile(ws)) == 1


@pytest.mark.parametrize(
    "text",
    [
        "The CI log is at https://ci.example.com/build.log:443 for build 12.",
        "Fixture events live in test/fixtures/events.jsonl:12.",
        "The handler is logs/handler.py:12.",
        "Logs go to `tmp/` and `.agent/` and are never committed.",
    ],
)
def test_urls_fixtures_and_source_packages_are_not_volatile(text: str) -> None:
    ws = _ready_workspace()
    _append(ws, _VERIFICATION, text)

    assert _volatile(ws) == []


@pytest.mark.parametrize(
    "text",
    [
        "the stage's durations span 753 to 1551 ms over 38 append-only ledger rows in "
        "tmp/verify-stages.ndjson (median 945 ms)",
        "tmp/verify-stages.ndjson holds 21,964 rows",
        "65 entries of .agent/raw/opencode.log show the retry",
    ],
)
def test_a_ledger_row_count_beside_a_volatile_record_is_a_finding(text: str) -> None:
    ws = _ready_workspace()
    _append(ws, _VERIFICATION, text)

    assert len(_volatile(ws)) == 1


def test_a_policy_copy_without_the_durable_evidence_rule_is_told_to_adopt_it() -> None:
    ws = _ready_workspace()
    path = f"{markers.CANONICAL_DIR}{_VERIFICATION}"
    body = "\n".join(
        line
        for line in ws.read(path).splitlines()
        if not line.startswith("RALPH-FACT: durable_evidence_rule:")
    )
    ws.write(path, body + "\n")

    findings = [
        f
        for f in validators.validate_readiness(ws, stack())
        if f.requirement_id.endswith("fact-durable_evidence_rule")
    ]

    assert len(findings) == 1
    assert findings[0].blocking is True
    assert "never cite" in findings[0].required_outcome
    assert "durable source" in findings[0].required_outcome


def test_a_volatile_citation_in_a_project_added_policy_file_blocks_readiness() -> None:
    ws = _ready_workspace()
    ws.write(
        f"{markers.CANONICAL_DIR}portfolio-admission.md",
        "# Portfolio admission\n\nBypass evidence: tmp/wt02-bypass-detection-20261123.log\n",
    )

    findings = _volatile(ws)

    assert [f.requirement_id for f in findings] == [
        f"{markers.ID_VOLATILE_EVIDENCE}:portfolio-admission.md"
    ]
    assert findings[0].blocking is True
