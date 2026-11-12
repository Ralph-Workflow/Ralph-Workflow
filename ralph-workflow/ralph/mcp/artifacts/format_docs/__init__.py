from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from pathlib import Path

FORMAT_DOC_ARTIFACT_TYPES: tuple[str, ...] = (
    "commit_message",
    "commit_review",
    "development_result",
    "issues",
    "fix_result",
    "design_verdict",
    "development_analysis_decision",
    "planning_analysis_decision",
    "review_analysis_decision",
    "policy_remediation_analysis_decision",
    "smoke_test_result",
    "run_time_report",
    "product_spec",
    "plan",
)

ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE = "artifact_formats_index"

FORMAT_DOCS_WORKSPACE_DIR = ".agent/artifact-formats"

EXAMPLE_ARTIFACT_TYPES: tuple[str, ...] = FORMAT_DOC_ARTIFACT_TYPES

EXAMPLES_WORKSPACE_DIR = f"{FORMAT_DOCS_WORKSPACE_DIR}/examples"


def format_doc_workspace_path(artifact_type: str) -> str:
    return f"{FORMAT_DOCS_WORKSPACE_DIR}/{artifact_type}.md"


def format_index_workspace_path() -> str:
    return f"{FORMAT_DOCS_WORKSPACE_DIR}/{ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE}.md"


def example_workspace_path(artifact_type: str) -> str:
    return f"{EXAMPLES_WORKSPACE_DIR}/{artifact_type}.md"


def load_bundled_example(artifact_type: str) -> str | None:
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
    content = load_bundled_example(artifact_type)
    if content is None:
        return None
    dest = workspace_root / EXAMPLES_WORKSPACE_DIR / f"{artifact_type}.md"
    write_text_if_changed(
        backend,
        dest,
        content,
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(dest.parent, parents=True, exist_ok=True),
    )
    return example_workspace_path(artifact_type)


def load_bundled_format_doc(artifact_type: str) -> str | None:
    if artifact_type not in FORMAT_DOC_ARTIFACT_TYPES:
        return None
    pkg = importlib.resources.files("ralph.mcp.artifacts.format_docs")
    resource = pkg.joinpath(f"{artifact_type}.md")
    return resource.read_text(encoding="utf-8")


def load_bundled_format_index() -> str:
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
    write_text_if_changed(
        backend,
        dest,
        content,
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(dest.parent, parents=True, exist_ok=True),
    )
    return format_doc_workspace_path(artifact_type)


def materialize_format_index(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    content = load_bundled_format_index()
    dest = workspace_root / FORMAT_DOCS_WORKSPACE_DIR / f"{ARTIFACT_FORMAT_INDEX_ARTIFACT_TYPE}.md"
    write_text_if_changed(
        backend,
        dest,
        content,
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(dest.parent, parents=True, exist_ok=True),
    )
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
