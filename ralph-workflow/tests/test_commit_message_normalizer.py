import pytest

from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.prompts.commit_evidence import CommitEvidenceBundle

EVIDENCE = CommitEvidenceBundle("diff", ("ralph/app.py",), ("ralph",), (), ("ralph/app.py",))


def _fact_evidence() -> CommitEvidenceBundle:
    return CommitEvidenceBundle(
        "diff",
        ("ralph/app.py",),
        ("ralph/app",),
        (),
        ("ralph/app.py",),
        behavior_facts=("Preserve retry evidence for submit artifacts.",),
    )


def test_normalizer_repairs_subject_and_replaces_stale_files() -> None:
    result = normalize_commit_message_draft("Subject: FIX: Preserve evidence\n\n- old.py", EVIDENCE)

    assert "subject: fix: preserve evidence" in result.content
    assert "ralph/app.py" in result.content
    assert result.confidence == "high"


def test_normalizer_rejects_ambiguous_intent() -> None:
    with pytest.raises(ValueError, match="intent is ambiguous"):
        normalize_commit_message_draft("please commit this", EVIDENCE)


def test_normalizer_rejects_unsupported_body_claims() -> None:
    with pytest.raises(ValueError, match="unsupported body claim"):
        normalize_commit_message_draft(
            "fix: preserve evidence\n\n## Body\n- [B-1] Retains the user-visible retry result.",
            EVIDENCE,
        )


def test_normalizer_preserves_high_confidence_grounded_body_claim_silently() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Changes\n* Preserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for submit artifacts." in result.content
    assert all(item.confidence == "high" for item in result.transformations)


def test_normalizer_preserves_partial_overlap_claim_and_flags_medium() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Changes\n* Preserve retry evidence for users.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for users." in result.content
    assert result.confidence == "medium"
    assert any(item.confidence == "medium" and item.source == "live evidence" for item in result.transformations)


def test_normalizer_reports_all_regeneration_diagnostic_components() -> None:
    with pytest.raises(ValueError) as raised:
        normalize_commit_message_draft("please commit this", EVIDENCE, draft_revision=3)

    message = str(raised.value)
    for component in (
        "expected shape:",
        "actual value/claim:",
        "evidence considered:",
        "attempted normalization:",
        "draft revision: 3",
        "minimal valid repair:",
    ):
        assert component in message


def test_normalizer_accepts_plain_prose_and_is_idempotent() -> None:
    first = normalize_commit_message_draft(
        "fix: preserve evidence\n\nPreserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )
    second = normalize_commit_message_draft(first.content, _fact_evidence())

    assert "Preserve retry evidence for submit artifacts." in first.content
    assert second.content == first.content
    assert second.transformations == ()


def test_normalizer_accepts_nonstandard_list_heading() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Notes\n- Preserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for submit artifacts." in result.content


def test_normalizer_regression_extracts_grounded_numbered_list_claim() -> None:
    """DA-004/S-5: numbered candidates follow the same evidence reconciliation path."""
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Notes\n1. Preserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for submit artifacts." in result.content


def test_normalizer_regression_rejects_unsupported_numbered_list_claim() -> None:
    """DA-004/S-5: numbered syntax must not bypass unsupported-claim rejection."""
    with pytest.raises(ValueError, match="unsupported body claim"):
        normalize_commit_message_draft(
            "fix: preserve evidence\n\n## Notes\n1. Invent nonexistent behavior.",
            _fact_evidence(),
        )


def test_normalizer_extracts_key_value_fields() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\nintent: Preserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for submit artifacts." in result.content


def test_normalizer_is_idempotent() -> None:
    first = normalize_commit_message_draft("fix: preserve evidence", EVIDENCE)
    second = normalize_commit_message_draft(first.content, EVIDENCE)

    assert second.content == first.content
    assert second.transformations == ()


def test_normalizer_regression_reconciles_canonical_stale_files() -> None:
    """DA-004/DA-005: canonical markers must not bypass live-file normalization."""
    result = normalize_commit_message_draft(
        "---\ntype: commit\nsubject: fix: preserve evidence\n---\n\n## Body\n"
        "- [B-1] Preserve retry evidence for submit artifacts.\n\n## Files\n- [F-1] old.py\n",
        _fact_evidence(),
    )

    assert "ralph/app.py" in result.content
    assert "old.py" not in result.content


def test_normalizer_regression_repairs_canonical_subject_without_separator() -> None:
    """DA-004/DA-006: canonical structure still receives deterministic subject repair."""
    result = normalize_commit_message_draft(
        "---\ntype: commit\nsubject: FIX Preserve evidence\n---\n",
        EVIDENCE,
    )

    assert "subject: fix: preserve evidence" in result.content


def test_normalizer_regression_accepts_uppercase_subject_without_separator() -> None:
    """DA-001: uppercase conventional kinds remain recoverable without a colon.

    Portfolio decision: KEEP the existing pure normalizer regression family;
    this case adds distinct case-insensitive extraction fault sensitivity.
    """
    result = normalize_commit_message_draft(
        "FIX Preserve retry evidence\n\nPreserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )

    assert "subject: fix: preserve retry evidence" in result.content


def test_normalizer_regression_rejects_unsupported_canonical_claim() -> None:
    """DA-005: canonical markers must not launder unsupported claims."""
    with pytest.raises(ValueError, match="unsupported body claim"):
        normalize_commit_message_draft(
            "---\ntype: commit\nsubject: fix: preserve evidence\n---\n\n## Body\n"
            "- [B-1] Invent nonexistent behavior.\n",
            _fact_evidence(),
        )
