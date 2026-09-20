import pytest

from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.prompts.commit_evidence import CommitEvidenceBundle

EVIDENCE = CommitEvidenceBundle("diff", ("ralph/app.py",), ("ralph",), (), ("ralph/app.py",))


def test_normalizer_repairs_subject_and_replaces_stale_files() -> None:
    result = normalize_commit_message_draft("Subject: FIX: Preserve evidence\n\n- old.py", EVIDENCE)

    assert "subject: fix: preserve evidence" in result.content
    assert "ralph/app.py" in result.content
    assert result.confidence == "high"


def test_normalizer_rejects_ambiguous_intent() -> None:
    with pytest.raises(ValueError, match="intent is ambiguous"):
        normalize_commit_message_draft("please commit this", EVIDENCE)


def test_normalizer_preserves_explicit_body_claims() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Body\n- [B-1] Retains the user-visible retry result.", EVIDENCE
    )

    assert "Retains the user-visible retry result." in result.content


def test_normalizer_reports_evidence_specific_regeneration_diagnostic() -> None:
    with pytest.raises(ValueError, match="commit evidence regeneration required"):
        normalize_commit_message_draft("please commit this", EVIDENCE)


def test_normalizer_is_idempotent() -> None:
    first = normalize_commit_message_draft("fix: preserve evidence", EVIDENCE)
    second = normalize_commit_message_draft(first.content, EVIDENCE)

    assert second.content == first.content
    assert second.transformations == ()
