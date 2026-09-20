"""Behavioral contracts for the evidence-first commit pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ralph.mcp.tools.commit_normalization as commit_normalization_module
from ralph.mcp.artifacts import submit_artifact_canonical
from ralph.mcp.artifacts.commit_message_ir import build_commit_message_ir
from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.mcp.artifacts.completion_receipts import (
    commit_receipt_matches_changed_files,
    write_artifact_receipt,
)
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.prompts.commit_evidence import CommitEvidenceBundle, CommitMessageBudget


def _evidence(*paths: str) -> CommitEvidenceBundle:
    return CommitEvidenceBundle(
        "diff",
        paths,
        tuple(sorted({path.rsplit("/", 1)[0] for path in paths})),
        (),
        tuple(path for path in paths if path.startswith("ralph/")),
        behavior_facts=("Preserve retry evidence.",),
        verification_facts=("focused tests passed",),
        fact_provenance=(
            ("behavior", "Preserve retry evidence.", "development_result", "high"),
            ("verification", "focused tests passed", "parallel summary", "high"),
        ),
    )


@pytest.mark.parametrize(
    ("paths", "expected_budget"),
    [
        (("docs/guide.md",), CommitMessageBudget("medium", 3)),
        (("ralph/api.py", "tests/test_api.py"), CommitMessageBudget("medium", 3)),
        (
            (
                "ralph/api.py",
                "ralph/git/ops.py",
                "ralph/mcp/tool.py",
                "ralph/pipeline/run.py",
                "tests/test_api.py",
            ),
            CommitMessageBudget("large", 7),
        ),
    ],
)
def test_evidence_sets_a_proportional_detail_budget(
    paths: tuple[str, ...], expected_budget: CommitMessageBudget
) -> None:
    assert _evidence(*paths).message_budget == expected_budget


def test_ir_keeps_required_categories_and_fact_attribution() -> None:
    evidence = _evidence("ralph/app.py")

    ir = build_commit_message_ir(evidence, subject="fix(app): preserve evidence")

    assert ir.intent
    assert ir.change_areas and ir.rationale and ir.behavior_risk and ir.verification and ir.files
    assert ir.fact_provenance == evidence.fact_provenance


def test_normalization_recovers_plain_prose_and_replaces_partial_files() -> None:
    evidence = _evidence("ralph/app.py", "tests/test_app.py")

    normalized = normalize_commit_message_draft(
        "fix(app): preserve evidence\n\nChanges:\n* Preserve retry evidence.\n\nFiles:\n* stale.py",
        evidence,
    )

    assert "subject: fix(app): preserve evidence" in normalized.content
    assert "ralph/app.py" in normalized.content
    assert "tests/test_app.py" in normalized.content
    assert "stale.py" not in normalized.content


@pytest.mark.subprocess_e2e
def test_receipt_requires_exact_live_file_identity(tmp_path: Path) -> None:
    audit: dict[str, object] = {"changed_files": ["ralph/app.py"], "confidence": "high"}
    write_artifact_receipt(tmp_path, "commit-plumbing", "commit_message", normalization_audit=audit)

    assert commit_receipt_matches_changed_files(
        tmp_path, "commit-plumbing", "commit_message", ("ralph/app.py",)
    ) is True
    assert commit_receipt_matches_changed_files(
        tmp_path, "commit-plumbing", "commit_message", ("ralph/other.py",)
    ) is False


def test_normalization_audit_records_fresh_evidence_and_draft_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DA-005/S-6: the canonical receipt gets the exact fresh-evidence audit."""
    monkeypatch.setattr(commit_normalization_module, "_build_evidence", lambda _root: _evidence("ralph/app.py"))
    (tmp_path / ".git").mkdir()

    content, audit = commit_normalization_module.normalize_commit_submission(
        "commit_message",
        "fix: preserve evidence\n\n## Notes\n1. Preserve retry evidence.",
        tmp_path,
        draft_revision=2,
    )

    assert "subject: fix: preserve evidence" in content
    assert audit is not None
    assert audit["changed_files"] == ["ralph/app.py"]
    assert audit["draft_revision"] == 2
    assert audit["provenance"] == ("live evidence", "draft")


def test_normalization_audit_flags_partial_overlap_at_medium_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commit_normalization_module, "_build_evidence", lambda _root: _evidence("ralph/app.py"))
    (tmp_path / ".git").mkdir()

    _, audit = commit_normalization_module.normalize_commit_submission(
        "commit_message",
        "fix: preserve evidence\n\n## Notes\n- Preserve retry evidence for users.",
        tmp_path,
    )

    assert audit is not None
    assert audit["confidence"] == "medium"
    assert audit["transformations"] == [
        {"action": "rendered canonical artifact from live evidence", "source": "live evidence", "confidence": "high"},
        {"action": "preserved partial-overlap draft claim", "source": "live evidence", "confidence": "medium"},
        {"action": "expanded body with grounded evidence facts", "source": "live evidence", "confidence": "high"},
    ]


def test_canonical_submission_persists_first_normalization_pass_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DA-007/S-2: canonical receipt keeps the single fresh normalization pass."""
    monkeypatch.setattr(commit_normalization_module, "_build_evidence", lambda _root: _evidence("ralph/app.py"))
    (tmp_path / ".git").mkdir()
    content, audit = commit_normalization_module.normalize_commit_submission(
        "commit_message",
        "fix: preserve evidence\n\n## Notes\n1. Preserve retry evidence.",
        tmp_path,
        draft_revision=2,
    )
    assert audit is not None
    parsed, diagnostics = parse_and_validate(content, get_spec("commit_message"))
    assert not [item for item in diagnostics if item.severity == "error"]

    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="commit_message",
        parsed_content=dict(parsed),
        markdown=content,
        run_id="commit-plumbing",
        normalization_audit=audit,
    )

    db = RunStateDB(tmp_path)
    try:
        stored = db.get_receipt_normalization_audit("commit-plumbing", "commit_message")
    finally:
        db.close()
    assert stored is not None
    assert json.loads(stored)["transformations"] == audit["transformations"]


def test_receipt_audit_is_json_serializable() -> None:
    audit: dict[str, object] = {"changed_files": ["ralph/app.py"], "transformations": ["canonicalized"]}
    assert json.dumps(audit, sort_keys=True)
