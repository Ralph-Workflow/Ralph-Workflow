from pathlib import Path

from ralph.mcp.artifacts.commit_message import read_commit_message_payload_from_path
from ralph.mcp.artifacts.commit_message_ir import (
    build_commit_message_ir,
    render_commit_message_artifact,
)
from ralph.prompts.commit_evidence import CommitEvidenceBundle


def test_evidence_ir_render_round_trip_preserves_grounded_files(tmp_path: Path) -> None:
    evidence = CommitEvidenceBundle("diff", ("ralph/app.py",), ("ralph",), ("pytest",), ("ralph/app.py",))
    artifact = render_commit_message_artifact(build_commit_message_ir(evidence, subject="fix: preserve evidence"))

    artifact_path = tmp_path / "commit_message.md"
    artifact_path.write_text(artifact, encoding="utf-8")

    assert read_commit_message_payload_from_path(artifact_path) == {
        "type": "commit", "subject": "fix: preserve evidence", "body": "pytest", "files": ["ralph/app.py"]
    }


def test_ir_renderer_preserves_excluded_files() -> None:
    from ralph.mcp.artifacts.commit_message_ir import CommitMessageIR

    artifact = render_commit_message_artifact(
        CommitMessageIR("fix: preserve evidence", (), (), (), (), (), (("docs/private.md", "internal_ignore"),))
    )

    assert "## Excluded Files" in artifact
    assert "docs/private.md | internal_ignore" in artifact
