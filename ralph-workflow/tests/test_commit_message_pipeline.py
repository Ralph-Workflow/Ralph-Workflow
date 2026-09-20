"""Behavioral contracts for the evidence-first commit pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.artifacts.commit_message_ir import build_commit_message_ir
from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.mcp.artifacts.completion_receipts import (
    commit_receipt_matches_changed_files,
    write_artifact_receipt,
)
from ralph.prompts.commit_evidence import CommitEvidenceBundle


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
        (("docs/guide.md",), "small: one focused body point"),
        (("ralph/api.py", "tests/test_api.py"), "medium: two or three focused body points"),
        (
            (
                "ralph/api.py",
                "ralph/git/ops.py",
                "ralph/mcp/tool.py",
                "ralph/pipeline/run.py",
                "tests/test_api.py",
            ),
            "large: cover each material area, risks, and verification",
        ),
    ],
)
def test_evidence_sets_a_proportional_detail_budget(paths: tuple[str, ...], expected_budget: str) -> None:
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


def test_receipt_audit_is_json_serializable() -> None:
    audit: dict[str, object] = {"changed_files": ["ralph/app.py"], "transformations": ["canonicalized"]}
    assert json.dumps(audit, sort_keys=True)
