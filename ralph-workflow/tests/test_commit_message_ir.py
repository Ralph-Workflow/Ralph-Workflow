from dataclasses import replace
from pathlib import Path

from ralph.mcp.artifacts.commit_message import read_commit_message_payload_from_path
from ralph.mcp.artifacts.commit_message_ir import (
    build_commit_message_ir,
    render_commit_message_artifact,
)
from ralph.prompts.commit_evidence import CommitEvidenceBundle, CommitMessageBudget


def test_evidence_ir_render_round_trip_omits_default_file_inventory(tmp_path: Path) -> None:
    evidence = CommitEvidenceBundle(
        "diff", ("ralph/app.py",), ("ralph",), (), ("ralph/app.py",), verification_facts=("pytest",)
    )
    artifact = render_commit_message_artifact(build_commit_message_ir(evidence, subject="fix: preserve evidence"))

    artifact_path = tmp_path / "commit_message.md"
    artifact_path.write_text(artifact, encoding="utf-8")

    payload = read_commit_message_payload_from_path(artifact_path)
    assert payload is not None
    assert payload["type"] == "commit"
    assert payload["subject"] == "fix: preserve evidence"
    assert "files" not in payload
    assert "pytest" in str(payload["body"])


def test_ir_renderer_consolidates_small_budget_without_losing_categories() -> None:
    ir = build_commit_message_ir(CommitEvidenceBundle(
        "diff", ("docs/guide.md",), ("docs",), (), (),
        behavior_facts=("Preserves compatibility.",), verification_facts=("pytest passed",),
    ), subject="fix: preserve evidence")
    artifact = render_commit_message_artifact(replace(ir, message_budget=CommitMessageBudget("small", 1)))

    assert artifact.count("- [B-") == 1
    assert "Preserves compatibility." in artifact
    assert "pytest passed" in artifact


def test_ir_renderer_regression_expands_large_budget_into_category_items() -> None:
    """DA-002/DA-008: available budget renders focused category items."""
    evidence = CommitEvidenceBundle(
        "diff",
        ("ralph/app.py", "ralph/mcp/tool.py", "ralph/pipeline/run.py", "tests/test_app.py"),
        ("ralph/app", "ralph/mcp", "ralph/pipeline", "tests"),
        (),
        ("ralph/app.py", "ralph/mcp/tool.py", "ralph/pipeline/run.py"),
        behavior_facts=("Preserves compatibility.",),
        verification_facts=("pytest passed",),
    )
    small = render_commit_message_artifact(
        replace(build_commit_message_ir(evidence, subject="fix: preserve evidence"), message_budget=CommitMessageBudget("small", 1))
    )
    large = render_commit_message_artifact(build_commit_message_ir(evidence, subject="fix: preserve evidence"))

    assert small.count("- [B-") == 1
    assert large.count("- [B-") > small.count("- [B-")
    assert large.count("- [B-") == 2
    assert "Preserves compatibility." in large
    assert "pytest passed" in large


def test_ir_renderer_preserves_excluded_files() -> None:
    from ralph.mcp.artifacts.commit_message_ir import CommitMessageIR

    artifact = render_commit_message_artifact(
        CommitMessageIR("fix: preserve evidence", (), (), (), (), (), (("docs/private.md", "internal_ignore"),))
    )

    assert "## Excluded Files" in artifact
    assert "docs/private.md | internal_ignore" in artifact
