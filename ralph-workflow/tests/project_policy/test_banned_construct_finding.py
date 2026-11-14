from __future__ import annotations

from ralph.project_policy import markers, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy import policy_corpus


def test_banned_construct_uncovered_finding_id_constant_pinned() -> None:
    assert markers.BANNED_CONSTRUCT_UNCOVERED_FINDING_ID == "RWP-LINT:BANNED-CONSTRUCT-UNCOVERED"


def test_seeded_complete_corpus_passes_without_banned_construct_finding() -> None:
    ws = MemoryWorkspace()
    policy_corpus.seed_complete_corpus(ws)
    findings = validators.validate_readiness(ws, policy_corpus.stack())
    banned_findings = [
        f for f in findings if f.requirement_id == markers.BANNED_CONSTRUCT_UNCOVERED_FINDING_ID
    ]
    assert banned_findings == []


def test_missing_language_in_ban_table_fires_finding() -> None:
    ws = MemoryWorkspace()
    policy_corpus.seed_complete_corpus(ws)
    clean_code_path = f"{markers.CANONICAL_DIR}clean-code-policy.md"
    content = ws.read(clean_code_path)
    modified = content.replace("| Python |", "| Rust |")
    ws.write(clean_code_path, modified)
    findings = validators.validate_readiness(ws, policy_corpus.stack())
    banned_findings = [
        f for f in findings if f.requirement_id == markers.BANNED_CONSTRUCT_UNCOVERED_FINDING_ID
    ]
    assert len(banned_findings) == 1
    assert "Python" in banned_findings[0].missing_evidence


def test_unknown_enforcer_in_ban_table_fires_finding() -> None:
    ws = MemoryWorkspace()
    policy_corpus.seed_complete_corpus(ws)
    clean_code_path = f"{markers.CANONICAL_DIR}clean-code-policy.md"
    content = ws.read(clean_code_path)
    modified = content.replace("deterministic: make test", "deterministic: non-existent-command")
    ws.write(clean_code_path, modified)
    findings = validators.validate_readiness(ws, policy_corpus.stack())
    banned_findings = [
        f for f in findings if f.requirement_id == markers.BANNED_CONSTRUCT_UNCOVERED_FINDING_ID
    ]
    assert len(banned_findings) == 1
    assert "non-existent-command" in banned_findings[0].missing_evidence


def test_missing_ban_table_heading_fires_finding() -> None:
    ws = MemoryWorkspace()
    policy_corpus.seed_complete_corpus(ws)
    clean_code_path = f"{markers.CANONICAL_DIR}clean-code-policy.md"
    ws.write(clean_code_path, "# Clean Code Policy\n\nNo ban table here.\n")
    findings = validators.validate_readiness(ws, policy_corpus.stack())
    banned_findings = [
        f for f in findings if f.requirement_id == markers.BANNED_CONSTRUCT_UNCOVERED_FINDING_ID
    ]
    assert len(banned_findings) == 1
    assert banned_findings[0].requirement_id == markers.BANNED_CONSTRUCT_UNCOVERED_FINDING_ID
    assert "Python" in banned_findings[0].missing_evidence
