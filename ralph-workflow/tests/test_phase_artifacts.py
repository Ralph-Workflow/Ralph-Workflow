from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace


def test_load_phase_artifact_raises_phase_artifact_error_when_file_missing(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)

    with pytest.raises(PhaseArtifactError, match="Artifact not found"):
        load_phase_artifact(workspace, ".agent/artifacts/missing.md")


def test_phase_artifacts_regression_loads_validated_markdown_as_legacy_envelope() -> None:
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/FIX_RESULT.md",
        """---
type: fix_result
---
## Summary
- [S1] Fixed validation
## Files Changed
- [F1] ralph/phases/artifacts.py
""",
    )

    artifact = load_phase_artifact(workspace, ".agent/FIX_RESULT.md")

    assert artifact == {
        "type": "fix_result",
        "content": {
            "summary": "Fixed validation",
            "files_changed": "- ralph/phases/artifacts.py",
        },
    }
    assert (
        unwrap_phase_artifact_content(artifact, expected_type="fix_result") == artifact["content"]
    )


def test_phase_artifacts_regression_reports_markdown_validation_errors() -> None:
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/FIX_RESULT.md",
        """---
type: fix_result
---
## Summary
- [S1] Fixed validation
""",
    )

    with pytest.raises(PhaseArtifactError, match=r"Markdown artifact .*missing required section"):
        load_phase_artifact(workspace, ".agent/FIX_RESULT.md")


def test_load_phase_artifact_with_explicit_artifact_type_loads_commit_message_markdown() -> None:
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/artifacts/commit_message.md",
        """---
type: commit
subject: fix(auth): prevent token expiry race
---
""",
    )

    artifact = load_phase_artifact(
        workspace,
        ".agent/artifacts/commit_message.md",
        artifact_type="commit_message",
    )

    assert artifact == {
        "type": "commit_message",
        "content": {"type": "commit", "subject": "fix(auth): prevent token expiry race"},
    }


def test_load_phase_artifact_with_explicit_artifact_type_still_reports_validation_errors() -> None:
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/artifacts/commit_message.md",
        """---
type: commit
subject: not a conventional subject
---
""",
    )

    with pytest.raises(PhaseArtifactError, match=r"Markdown artifact .* is invalid"):
        load_phase_artifact(
            workspace,
            ".agent/artifacts/commit_message.md",
            artifact_type="commit_message",
        )


def test_load_phase_artifact_with_unknown_explicit_artifact_type_raises() -> None:
    workspace = MemoryWorkspace()
    workspace.write(".agent/artifacts/mystery.md", "---\ntype: commit\n---\n")

    with pytest.raises(PhaseArtifactError, match="Unsupported markdown artifact type"):
        load_phase_artifact(workspace, ".agent/artifacts/mystery.md", artifact_type="mystery")


def test_phase_artifacts_regression_rejects_legacy_json_without_loading_it() -> None:
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/artifacts/fix_result.json",
        '{"type":"fix_result","content":{"summary":"Kept","files_changed":"x.py"}}',
    )

    with pytest.raises(
        PhaseArtifactError,
        match=r"unsupported legacy JSON.*re-author.*fix_result\.md",
    ):
        load_phase_artifact(workspace, ".agent/artifacts/fix_result.json")
