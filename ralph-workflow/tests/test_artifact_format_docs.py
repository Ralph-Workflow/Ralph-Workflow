"""Tests for ralph/mcp/artifacts/format_docs.py — bundled format doc module."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.artifacts.commit_message import normalize_commit_message_content
from ralph.mcp.artifacts.development_result import normalize_development_result_content
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.format_docs import (
    FORMAT_DOC_ARTIFACT_TYPES,
    format_doc_workspace_path,
    format_index_workspace_path,
    load_bundled_format_doc,
    load_bundled_format_index,
    materialize_all_format_docs,
    materialize_format_doc,
    materialize_format_index,
)
from ralph.mcp.tools.artifact import handle_submit_artifact
from ralph.mcp.tools.coordination import InvalidParamsError

MIN_CHECKLIST_BULLETS = 3


class MemoryBackend(FileBackend):
    def __init__(self) -> None:
        self._files: dict[Path, str] = {}
        self._directories: set[Path] = set()

    def exists(self, path: Path) -> bool:
        return path in self._files or path in self._directories

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del exist_ok
        self._directories.add(path)
        if parents:
            self._directories.update(path.parents)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self._directories.add(path.parent)
        self._directories.update(path.parent.parents)
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self._directories.add(destination.parent)
        self._directories.update(destination.parent.parents)
        self._files[destination] = self._files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        if pattern != "*.json":
            return []
        prefix = f"{path}/"
        return [
            candidate
            for candidate in self._files
            if str(candidate).startswith(prefix) and candidate.suffix == ".json"
        ]


@dataclass
class MockSession:
    session_id: str = "test-session"
    drain: str = "development"

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"


class MockWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def absolute_path(self, path: str) -> str:
        return str(self.root / path)


def _extract_complete_example_inner_payload(doc: str) -> dict[str, object]:
    parts = doc.split("## Complete example")
    assert len(parts) > 1, "Missing '## Complete example' section"
    section = parts[1]
    match = re.search(r"```json\n(.*?)```", section, re.DOTALL)
    assert match is not None, "No ```json block in '## Complete example' section"
    outer = json.loads(match.group(1))
    assert isinstance(outer, dict) and "content" in outer
    inner = json.loads(cast("str", outer["content"]))
    assert isinstance(inner, dict)
    return cast("dict[str, object]", inner)


def test_all_supported_artifact_types_have_bundled_markdown() -> None:
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        doc = load_bundled_format_doc(artifact_type)
        assert doc is not None, f"No bundled doc for {artifact_type!r}"
        assert len(doc) > 0, f"Bundled doc for {artifact_type!r} is empty"
        assert "artifact format" in doc, (
            f"Bundled doc for {artifact_type!r} missing '# ... artifact format' heading"
        )


def test_load_bundled_format_doc_returns_none_for_unsupported_type() -> None:
    assert load_bundled_format_doc("plan") is None
    assert load_bundled_format_doc("bogus") is None
    assert load_bundled_format_doc("") is None


def test_materialize_format_doc_writes_markdown_to_workspace() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    relative_path = materialize_format_doc(workspace_root, "commit_message", backend=backend)

    assert relative_path == ".agent/artifact-formats/commit_message.md"
    expected_content = load_bundled_format_doc("commit_message")
    assert expected_content is not None
    assert (
        backend.read_text(workspace_root / ".agent/artifact-formats/commit_message.md")
        == expected_content
    )


def test_materialize_format_doc_is_idempotent() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    first = materialize_format_doc(workspace_root, "commit_message", backend=backend)
    content_after_first = backend.read_text(
        workspace_root / ".agent/artifact-formats/commit_message.md"
    )
    second = materialize_format_doc(workspace_root, "commit_message", backend=backend)
    content_after_second = backend.read_text(
        workspace_root / ".agent/artifact-formats/commit_message.md"
    )

    assert first == second
    assert content_after_first == content_after_second


def test_materialize_format_doc_returns_none_for_unsupported() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    assert materialize_format_doc(workspace_root, "plan", backend=backend) is None
    assert materialize_format_doc(workspace_root, "bogus", backend=backend) is None
    assert not any(
        str(p).endswith("plan.md") or str(p).endswith("bogus.md")
        for p in backend._files
    )


def test_materialize_all_format_docs_materializes_every_supported_type() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    paths = materialize_all_format_docs(workspace_root, backend=backend)

    # 6 per-type docs + 1 index doc = 7
    assert len(paths) == len(FORMAT_DOC_ARTIFACT_TYPES) + 1
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        expected_path = format_doc_workspace_path(artifact_type)
        assert expected_path in paths
        assert backend.exists(workspace_root / expected_path)
    # Index doc is also included
    assert format_index_workspace_path() in paths
    assert backend.exists(workspace_root / format_index_workspace_path())


def test_bundled_examples_validate_through_real_normalizers(tmp_path: Path) -> None:
    normalizers = {
        "commit_message": normalize_commit_message_content,
        "development_result": normalize_development_result_content,
    }
    passthrough_types = {
        "issues",
        "fix_result",
        "development_analysis_decision",
        "review_analysis_decision",
    }

    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        doc = load_bundled_format_doc(artifact_type)
        assert doc is not None
        inner_payload = _extract_complete_example_inner_payload(doc)

        if artifact_type in normalizers:
            normalizers[artifact_type](inner_payload)
        else:
            assert artifact_type in passthrough_types
            result = handle_submit_artifact(
                MockSession(),
                MockWorkspace(tmp_path / artifact_type),
                {
                    "artifact_type": artifact_type,
                    "content": json.dumps(inner_payload),
                },
            )
            assert result.is_error is False


def test_format_doc_mentions_required_fields() -> None:
    required_fields: dict[str, list[str]] = {
        "commit_message": ["subject", "type", "reason"],
        "development_result": ["status", "summary", "files_changed"],
        "issues": ["path", "severity", "summary"],
        "fix_result": ["summary", "files_changed"],
        "development_analysis_decision": ["status", "summary", "what_came_up_short", "how_to_fix"],
        "review_analysis_decision": ["status", "summary", "what_came_up_short", "how_to_fix"],
    }

    for artifact_type, fields in required_fields.items():
        doc = load_bundled_format_doc(artifact_type)
        assert doc is not None
        for field in fields:
            assert field in doc, (
                f"Doc for {artifact_type!r} missing required field name {field!r}"
            )


def test_format_doc_workspace_path_returns_correct_relative_path() -> None:
    assert format_doc_workspace_path("commit_message") == (
        ".agent/artifact-formats/commit_message.md"
    )
    assert format_doc_workspace_path("issues") == ".agent/artifact-formats/issues.md"


def test_handle_submit_artifact_invalid_commit_message_points_to_format_doc(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidParamsError, match=r"\.agent/artifact-formats/commit_message\.md"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": json.dumps({"type": "commit"}),
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "commit_message.md").exists()
    content = (tmp_path / ".agent" / "artifact-formats" / "commit_message.md").read_text(
        encoding="utf-8"
    )
    assert content.startswith("# commit_message artifact format")


# --- New parametric redirect tests (Steps 9 & 10) ---


@pytest.mark.parametrize("artifact_type", FORMAT_DOC_ARTIFACT_TYPES)
def test_every_supported_artifact_type_redirects_on_bad_payload(
    artifact_type: str,
    tmp_path: Path,
) -> None:
    """Each known artifact type redirects to its format doc on validation failure."""
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path / artifact_type),
            {"artifact_type": artifact_type, "content": "{}"},
        )
    message = str(exc_info.value)
    expected_path = f".agent/artifact-formats/{artifact_type}.md"
    assert expected_path in message, f"Expected {expected_path} in message: {message}"
    # Raw validator text should NOT appear in the user-facing message
    assert "Field required" not in message
    assert "model_validate" not in message
    # Format doc was materialized
    doc_path = (
        tmp_path / artifact_type / ".agent" / "artifact-formats" / f"{artifact_type}.md"
    )
    assert doc_path.exists()


def test_unknown_artifact_type_redirects_to_index(tmp_path: Path) -> None:
    """Unknown artifact_type redirects to the index doc."""
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {"artifact_type": "not_a_real_type", "content": "{}"},
        )
    message = str(exc_info.value)
    assert ".agent/artifact-formats/artifact_formats_index.md" in message
    assert (tmp_path / ".agent" / "artifact-formats" / "artifact_formats_index.md").exists()


def test_missing_artifact_type_redirects_to_index(tmp_path: Path) -> None:
    """Missing artifact_type redirects to the index doc."""
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {"content": "{}"},
        )
    message = str(exc_info.value)
    assert ".agent/artifact-formats/artifact_formats_index.md" in message
    assert (tmp_path / ".agent" / "artifact-formats" / "artifact_formats_index.md").exists()


@pytest.mark.parametrize("artifact_type", ["issues", "development_result"])
def test_content_path_redirects_to_format_doc(
    artifact_type: str,
    tmp_path: Path,
) -> None:
    """content_path is not part of the agent-facing contract.

    It should redirect to the per-type format doc.
    """
    payload_path = tmp_path / "artifact.json"
    payload_path.write_text("{}", encoding="utf-8")
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": artifact_type,
                "content_path": str(payload_path),
            },
        )
    message = str(exc_info.value)
    expected_path = f".agent/artifact-formats/{artifact_type}.md"
    assert expected_path in message, f"Expected {expected_path} in message: {message}"
    assert (tmp_path / ".agent" / "artifact-formats" / f"{artifact_type}.md").exists()


@pytest.mark.parametrize("artifact_type", ["issues", "development_result"])
def test_missing_content_redirects_to_format_doc(
    artifact_type: str,
    tmp_path: Path,
) -> None:
    """Missing content redirects to per-type format doc."""
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {"artifact_type": artifact_type},
        )
    message = str(exc_info.value)
    expected_path = f".agent/artifact-formats/{artifact_type}.md"
    assert expected_path in message, f"Expected {expected_path} in message: {message}"
    assert (tmp_path / ".agent" / "artifact-formats" / f"{artifact_type}.md").exists()


def test_analysis_decision_without_drain_points_to_index(tmp_path: Path) -> None:
    """analysis_decision used outside of an analysis drain redirects to index."""
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(drain=""),  # empty drain - not an analysis drain
            MockWorkspace(tmp_path),
            {"artifact_type": "analysis_decision", "content": '{"status": "completed"}'},
        )
    message = str(exc_info.value)
    assert ".agent/artifact-formats/artifact_formats_index.md" in message
    # Both analysis decision docs should be materialized
    assert (
        tmp_path / ".agent" / "artifact-formats" / "development_analysis_decision.md"
    ).exists()
    assert (
        tmp_path / ".agent" / "artifact-formats" / "review_analysis_decision.md"
    ).exists()
    assert (tmp_path / ".agent" / "artifact-formats" / "artifact_formats_index.md").exists()


# --- Index materialization tests (Step 10) ---


def test_materialize_format_index_writes_umbrella_doc() -> None:
    """materialize_format_index writes the index doc to the workspace."""
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    relative_path = materialize_format_index(workspace_root, backend=backend)

    assert relative_path == ".agent/artifact-formats/artifact_formats_index.md"
    index_path = workspace_root / ".agent" / "artifact-formats" / "artifact_formats_index.md"
    content = backend.read_text(index_path)
    assert len(content) > 0
    assert "Artifact Formats Index" in content
    assert "commit_message" in content
    assert "development_result" in content


def test_materialize_all_format_docs_includes_index() -> None:
    """materialize_all_format_docs returns paths including the index doc."""
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    paths = materialize_all_format_docs(workspace_root, backend=backend)

    index_path = ".agent/artifact-formats/artifact_formats_index.md"
    assert index_path in paths


def test_load_bundled_format_index_returns_index_content() -> None:
    """load_bundled_format_index returns non-empty content with required sections."""
    content = load_bundled_format_index()
    assert len(content) > 0
    assert "Artifact Formats Index" in content
    # The index lists all the valid artifact types
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        assert artifact_type in content


# --- Uniform structure tests (Step 10) ---


REQUIRED_SECTIONS = [
    "## What you are doing",
    "## How to submit",
    "## Required fields",
    "## Optional fields",
    "## Complete example",
    "## Common mistakes",
]


@pytest.mark.parametrize("artifact_type", FORMAT_DOC_ARTIFACT_TYPES)
def test_every_format_doc_has_uniform_structure(artifact_type: str) -> None:
    """Each per-type format doc has all six required section headings."""
    doc = load_bundled_format_doc(artifact_type)
    assert doc is not None
    for section in REQUIRED_SECTIONS:
        assert section in doc, f"Doc {artifact_type!r} missing section: {section}"


def test_index_doc_has_required_sections() -> None:
    """The index doc has the required section headings."""
    content = load_bundled_format_index()
    assert "## What you are doing" in content
    assert "## How to submit" in content
    assert "## Required fields" in content
    assert "## Optional fields" in content
    assert "## Complete example" in content
    assert "## Common mistakes" in content
    assert "## Dumb-proof checklist" in content


@pytest.mark.parametrize("artifact_type", FORMAT_DOC_ARTIFACT_TYPES)
def test_every_format_doc_has_dumb_proof_checklist(artifact_type: str) -> None:
    """Each per-type format doc has a Dumb-proof checklist section."""
    doc = load_bundled_format_doc(artifact_type)
    assert doc is not None
    assert "## Dumb-proof checklist" in doc, (
        f"Doc {artifact_type!r} missing '## Dumb-proof checklist' section"
    )
    # The checklist should have some bullet points
    parts = doc.split("## Dumb-proof checklist")
    assert len(parts) > 1
    checklist_content = parts[1]
    # Check for at least MIN_CHECKLIST_BULLETS bullet points (lines starting with - or *)
    bullet_lines = [
        line.strip()
        for line in checklist_content.split("\n")
        if line.strip().startswith(("* ", "- "))
    ]
    assert (
        len(bullet_lines) >= MIN_CHECKLIST_BULLETS
    ), f"Doc {artifact_type!r} checklist has fewer than {MIN_CHECKLIST_BULLETS} bullets"
