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


def test_normalizer_repairs_subject_and_removes_stale_files() -> None:
    result = normalize_commit_message_draft("Subject: FIX: Preserve evidence\n\n- old.py", EVIDENCE)

    assert "subject: fix: preserve evidence" in result.content
    assert "## Files" not in result.content
    assert result.confidence == "high"


def test_normalizer_leaves_valid_safe_artifact_byte_identical() -> None:
    content = (
        "---\n"
        "type: commit\n"
        "subject: fix(commit): preserve the authored message\n"
        "---\n\n"
        "## Body\n"
        "- [B-1] Keeps a useful explanation of the change without generated directory narration.\n"
    )

    result = normalize_commit_message_draft(content, EVIDENCE)

    assert result.content == content
    assert result.transformations == ()
    assert result.provenance == ("draft",)


def test_normalizer_removes_file_inventory_even_when_it_matches_live_changes() -> None:
    content = (
        "---\n"
        "type: commit\n"
        "subject: fix(commit): preserve the authored message\n"
        "---\n\n"
        "## Body\n"
        "- [B-1] Keeps the useful explanation.\n\n"
        "## Files\n"
        "- [F-1] ralph/app.py\n"
    )

    result = normalize_commit_message_draft(content, EVIDENCE)

    assert "## Files" not in result.content
    assert "ralph/app.py" not in result.content


def test_normalizer_rejects_ambiguous_intent() -> None:
    with pytest.raises(ValueError, match="intent is ambiguous"):
        normalize_commit_message_draft("please commit this", EVIDENCE)


def test_normalizer_accepts_paraphrased_body_claim_without_token_overlap() -> None:
    """Body claims are not a hard validation boundary: paraphrases must survive."""
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Body\n- [B-1] Retains the user-visible retry result.",
        EVIDENCE,
    )

    assert "Retains the user-visible retry result." in result.content
    assert result.confidence == "high"


def test_normalizer_preserves_high_confidence_grounded_body_claim_silently() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Changes\n* Preserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for submit artifacts." in result.content
    assert all(item.confidence == "high" for item in result.transformations)


def test_normalizer_preserves_paraphrase_without_scoring_its_words() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Changes\n* Preserve retry evidence for users.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for users." in result.content
    assert result.confidence == "high"


def test_normalizer_preserves_authored_prose_without_keyword_classification() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Changes\n* artifacts submit preserve evidence retry for.",
        _fact_evidence(),
    )

    assert "artifacts submit preserve evidence retry for." in result.content
    assert result.confidence == "high"


def test_normalizer_treats_changed_path_anchor_as_high_support() -> None:
    """Contiguous path/area anchors are structural relevance, not token overlap."""
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Changes\n* Touches ralph/app.py scheduling owner write.",
        _fact_evidence(),
    )

    assert "Touches ralph/app.py scheduling owner write." in result.content
    assert all(
        item.action != "preserved relevant draft claim without exact evidence match"
        for item in result.transformations
    )
    assert all(item.confidence == "high" for item in result.transformations)


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


def test_normalizer_preserves_mixed_list_and_prose_body() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\nWhy this matters to operators.\n\n"
        "## Notes\n- Keeps retry results available.\n"
        "This continuation explains the operational impact.",
        _fact_evidence(),
    )

    assert "Why this matters to operators." in result.content
    assert "Keeps retry results available." in result.content
    assert "This continuation explains the operational impact." in result.content


def test_normalizer_repairs_quoted_conventional_subject() -> None:
    result = normalize_commit_message_draft(
        '---\ntype: commit\nsubject: "FIX: Preserve evidence"\n---\n\n'
        "## Body\n- [B-1] Keeps the authored explanation.\n",
        EVIDENCE,
    )

    assert "subject: fix: preserve evidence" in result.content
    assert "Keeps the authored explanation." in result.content


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


def test_normalizer_regression_accepts_architectural_numbered_list_claim() -> None:
    """DA-004/S-5: numbered architectural summaries survive without fact alignment."""
    result = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Notes\n1. Restructures write paths so drift is unrepresentable.",
        _fact_evidence(),
    )

    assert "Restructures write paths so drift is unrepresentable." in result.content
    assert result.confidence == "high"


def test_normalizer_extracts_key_value_fields() -> None:
    result = normalize_commit_message_draft(
        "fix: preserve evidence\nintent: Preserve retry evidence for submit artifacts.",
        _fact_evidence(),
    )

    assert "Preserve retry evidence for submit artifacts." in result.content


def test_normalizer_does_not_inject_evidence_or_drop_authored_body_items() -> None:
    evidence = CommitEvidenceBundle(
        "diff", ("docs/guide.md",), ("docs",), (), (),
        behavior_facts=("Preserves compatibility.",), verification_facts=("pytest passed",),
    )
    expanded = normalize_commit_message_draft("fix: preserve evidence", evidence)
    compressed = normalize_commit_message_draft(
        "fix: preserve evidence\n\n## Body\n- Changed docs.\n- Preserves compatibility.\n- pytest passed\n- pytest passed",
        evidence,
    )

    assert "Preserves compatibility." not in expanded.content
    assert "pytest passed" not in expanded.content
    assert "Changed docs." in compressed.content
    assert "Preserves compatibility." in compressed.content
    assert "pytest passed" in compressed.content
    assert normalize_commit_message_draft(compressed.content, evidence).transformations == ()


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

    assert "## Files" not in result.content
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


def test_normalizer_regression_accepts_canonical_architectural_claim() -> None:
    """DA-005: canonical markers must still accept non-overlapping architectural prose."""
    result = normalize_commit_message_draft(
        "---\ntype: commit\nsubject: fix: preserve evidence\n---\n\n## Body\n"
        "- [B-1] Restructures write paths so drift is unrepresentable.\n",
        _fact_evidence(),
    )

    assert "Restructures write paths so drift is unrepresentable." in result.content
    assert result.confidence == "high"


def test_normalizer_accepts_planning_architecture_summary_without_diff_keyword_match() -> None:
    """Regression: write-time schedule derivation prose must not fail keyword grounding."""
    evidence = CommitEvidenceBundle(
        "diff",
        ("app/services/planning/plan_scheduling.rb", "app/models/plan_activity.rb"),
        ("app/services", "app/models"),
        (),
        ("app/services/planning/plan_scheduling.rb", "app/models/plan_activity.rb"),
    )
    claim = (
        "Adds Planning::PlanScheduling.derive_and_assign_schedule! as the owner write; "
        "post-draft day-kind activities can never persist with a blank scheduled_on."
    )

    result = normalize_commit_message_draft(
        "fix(planning)!: prevent post-draft plan schedule drift\n\n"
        f"## Body Summary\n- [BS-1] {claim}\n",
        evidence,
    )

    assert claim in result.content
    assert "unsupported body claim" not in result.content
