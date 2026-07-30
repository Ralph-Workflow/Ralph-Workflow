"""Bundled dumb-proof Markdown reference docs for artifact submission."""

from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from pathlib import Path

FORMAT_DOC_ARTIFACT_TYPES: tuple[str, ...] = (
    "commit_message",
    "commit_cleanup",
    "development_result",
    "issues",
    "fix_result",
    "development_analysis_decision",
    "planning_analysis_decision",
    "review_analysis_decision",
    "policy_remediation_analysis_decision",
    "smoke_test_result",
    "product_spec",
    "plan",
)

ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE = "artifact_formats_index"

FORMAT_DOCS_WORKSPACE_DIR = ".agent/artifact-formats"

#: Artifact types that ship a full self-teaching example document alongside
#: their format doc. Each example is a complete, validator-passing markdown
#: artifact whose content also models the craft (a good plan, a good commit
#: message, honest proof discipline, and so on).
EXAMPLE_ARTIFACT_TYPES: tuple[str, ...] = FORMAT_DOC_ARTIFACT_TYPES

EXAMPLES_WORKSPACE_DIR = f"{FORMAT_DOCS_WORKSPACE_DIR}/examples"


def format_doc_workspace_path(artifact_type: str) -> str:
    """Return the workspace-relative path for an artifact format doc."""
    return f"{FORMAT_DOCS_WORKSPACE_DIR}/{artifact_type}.md"


def format_index_workspace_path() -> str:
    """Return the workspace-relative path for the artifact formats index doc."""
    return f"{FORMAT_DOCS_WORKSPACE_DIR}/{ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE}.md"


def example_workspace_path(artifact_type: str) -> str:
    """Return the workspace-relative path for a bundled example artifact."""
    return f"{EXAMPLES_WORKSPACE_DIR}/{artifact_type}.md"


def load_bundled_example(artifact_type: str) -> str | None:
    """Load a bundled example artifact document, or None if unknown."""
    if artifact_type not in EXAMPLE_ARTIFACT_TYPES:
        return None
    pkg = importlib.resources.files("ralph.mcp.artifacts.format_docs")
    resource = pkg.joinpath("examples").joinpath(f"{artifact_type}.md")
    return resource.read_text(encoding="utf-8")


def materialize_example(
    workspace_root: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    """Write a bundled example artifact into the workspace and return its relative path."""
    content = load_bundled_example(artifact_type)
    if content is None:
        return None
    dest = workspace_root / EXAMPLES_WORKSPACE_DIR / f"{artifact_type}.md"
    backend.mkdir(dest.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, dest, content, encoding="utf-8")
    return example_workspace_path(artifact_type)


def load_bundled_format_doc(artifact_type: str) -> str | None:
    """Load a bundled Markdown format doc for the given artifact type, or None if unknown."""
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
    """Write a bundled format doc into the workspace and return its relative path."""
    if artifact_type not in FORMAT_DOC_ARTIFACT_TYPES:
        return None
    content = load_bundled_format_doc(artifact_type)
    if content is None:
        return None
    dest = workspace_root / FORMAT_DOCS_WORKSPACE_DIR / f"{artifact_type}.md"
    backend.mkdir(dest.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, dest, content, encoding="utf-8")
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
    write_text_if_changed(backend, dest, content, encoding="utf-8")
    return format_index_workspace_path()


def materialize_all_format_docs(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> list[str]:
    """Write all bundled format docs, examples, and the index into the workspace."""
    result = []
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        path = materialize_format_doc(workspace_root, artifact_type, backend=backend)
        if path is not None:
            result.append(path)
    for artifact_type in EXAMPLE_ARTIFACT_TYPES:
        example_path = materialize_example(workspace_root, artifact_type, backend=backend)
        if example_path is not None:
            result.append(example_path)
    result.append(materialize_format_index(workspace_root, backend=backend))
    return result


__all__ = [
    "ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE",
    "EXAMPLES_WORKSPACE_DIR",
    "EXAMPLE_ARTIFACT_TYPES",
    "FORMAT_DOCS_WORKSPACE_DIR",
    "FORMAT_DOC_ARTIFACT_TYPES",
    "example_workspace_path",
    "format_doc_workspace_path",
    "format_index_workspace_path",
    "load_bundled_example",
    "load_bundled_format_doc",
    "load_bundled_format_index",
    "materialize_all_format_docs",
    "materialize_example",
    "materialize_format_doc",
    "materialize_format_index",
]
