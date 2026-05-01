"""Bundled dumb-proof Markdown reference docs for non-plan artifact submission."""

from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from pathlib import Path

FORMAT_DOC_ARTIFACT_TYPES: tuple[str, ...] = (
    "commit_message",
    "development_result",
    "issues",
    "fix_result",
    "development_analysis_decision",
    "planning_analysis_decision",
    "review_analysis_decision",
)

ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE = "artifact_formats_index"

FORMAT_DOCS_WORKSPACE_DIR = ".agent/artifact-formats"


def has_format_doc(artifact_type: str) -> bool:
    return artifact_type in FORMAT_DOC_ARTIFACT_TYPES


def format_doc_workspace_path(artifact_type: str) -> str:
    return f"{FORMAT_DOCS_WORKSPACE_DIR}/{artifact_type}.md"


def format_index_workspace_path() -> str:
    return f"{FORMAT_DOCS_WORKSPACE_DIR}/{ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE}.md"


def load_bundled_format_doc(artifact_type: str) -> str | None:
    if artifact_type not in FORMAT_DOC_ARTIFACT_TYPES:
        return None
    pkg = importlib.resources.files("ralph.mcp.artifacts.format_docs")
    resource = pkg.joinpath(f"{artifact_type}.md")
    return resource.read_text(encoding="utf-8")


def load_bundled_format_index() -> str:
    """Load the bundled artifact formats index doc."""
    pkg = importlib.resources.files("ralph.mcp.artifacts.format_docs")
    resource = pkg.joinpath(f"{ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE}.md")
    return resource.read_text(encoding="utf-8")


def materialize_format_doc(
    workspace_root: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    if artifact_type not in FORMAT_DOC_ARTIFACT_TYPES:
        return None
    content = load_bundled_format_doc(artifact_type)
    if content is None:
        return None
    dest = workspace_root / FORMAT_DOCS_WORKSPACE_DIR / f"{artifact_type}.md"
    backend.mkdir(dest.parent, parents=True, exist_ok=True)
    backend.write_text(dest, content, encoding="utf-8")
    return format_doc_workspace_path(artifact_type)


def materialize_format_index(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    """Materialize the bundled artifact formats index doc to workspace.

    Returns the relative path to the materialized index file.
    """
    content = load_bundled_format_index()
    dest = workspace_root / FORMAT_DOCS_WORKSPACE_DIR / f"{ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE}.md"
    backend.mkdir(dest.parent, parents=True, exist_ok=True)
    backend.write_text(dest, content, encoding="utf-8")
    return format_index_workspace_path()


def materialize_all_format_docs(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> list[str]:
    result = []
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        path = materialize_format_doc(workspace_root, artifact_type, backend=backend)
        if path is not None:
            result.append(path)
    result.append(materialize_format_index(workspace_root, backend=backend))
    return result


__all__ = [
    "ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE",
    "FORMAT_DOCS_WORKSPACE_DIR",
    "FORMAT_DOC_ARTIFACT_TYPES",
    "format_doc_workspace_path",
    "format_index_workspace_path",
    "has_format_doc",
    "load_bundled_format_doc",
    "load_bundled_format_index",
    "materialize_all_format_docs",
    "materialize_format_doc",
    "materialize_format_index",
]
