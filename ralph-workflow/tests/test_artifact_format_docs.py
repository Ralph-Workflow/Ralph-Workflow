"""Consistency checks for bundled markdown artifact format documentation."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from ralph.mcp.artifacts.format_docs import (
    EXAMPLE_ARTIFACT_TYPES,
    FORMAT_DOC_ARTIFACT_TYPES,
    example_workspace_path,
    format_doc_workspace_path,
    format_index_workspace_path,
    load_bundled_example,
    load_bundled_format_doc,
    load_bundled_format_index,
    materialize_all_format_docs,
    materialize_example,
    materialize_format_doc,
    materialize_format_index,
)
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs
from tests.test_artifact_format_docs_memory_backend import MemoryBackend

_POLICY_REMEDIATION_ANALYSIS_DECISION = "policy_remediation_analysis_decision"
_CLOSED_STATUS_VOCABULARIES = {
    "development_result": ("completed", "partial"),
    "planning_analysis_decision": ("completed", "request_changes", "failed"),
    "development_analysis_decision": ("completed", "request_changes", "failed"),
    "review_analysis_decision": ("completed", "request_changes", "failed"),
    "policy_remediation_analysis_decision": ("completed", "request_changes", "failed"),
    "smoke_test_result": ("passed", "failed", "partial"),
}


@pytest.mark.parametrize("artifact_type", FORMAT_DOC_ARTIFACT_TYPES)
def test_every_supported_type_has_a_nonempty_format_doc(artifact_type: str) -> None:
    doc = load_bundled_format_doc(artifact_type)
    assert doc is not None
    assert doc.startswith(f"# {artifact_type} artifact format")
    assert "ralph_submit_md_artifact" in doc
    assert "```markdown" in doc


def test_policy_remediation_analysis_decision_ships_a_validated_format_contract() -> None:
    assert _POLICY_REMEDIATION_ANALYSIS_DECISION in FORMAT_DOC_ARTIFACT_TYPES
    assert _POLICY_REMEDIATION_ANALYSIS_DECISION in EXAMPLE_ARTIFACT_TYPES

    doc = load_bundled_format_doc(_POLICY_REMEDIATION_ANALYSIS_DECISION)
    example = load_bundled_example(_POLICY_REMEDIATION_ANALYSIS_DECISION)
    index = load_bundled_format_index()

    assert doc is not None
    assert example is not None
    assert example_workspace_path(_POLICY_REMEDIATION_ANALYSIS_DECISION) in doc
    assert _POLICY_REMEDIATION_ANALYSIS_DECISION in index
    assert "json" not in doc.lower()
    assert "json" not in example.lower()

    import_module("ralph.mcp.artifacts.markdown.specs")
    _, diagnostics = parse_and_validate(
        example,
        get_spec(_POLICY_REMEDIATION_ANALYSIS_DECISION),
    )
    assert [item for item in diagnostics if item.severity == "error"] == []


@pytest.mark.parametrize("artifact_type", FORMAT_DOC_ARTIFACT_TYPES)
def test_every_format_doc_points_to_its_validator_backed_example(artifact_type: str) -> None:
    doc = load_bundled_format_doc(artifact_type)
    assert doc is not None
    assert example_workspace_path(artifact_type) in doc


@pytest.mark.parametrize("artifact_type", EXAMPLE_ARTIFACT_TYPES)
def test_every_bundled_example_validates_with_the_registered_spec(artifact_type: str) -> None:
    import_module("ralph.mcp.artifacts.markdown.specs")
    example = load_bundled_example(artifact_type)
    assert example is not None
    _, diagnostics = parse_and_validate(example, get_spec(artifact_type))
    assert [item for item in diagnostics if item.severity == "error"] == []


def test_format_doc_types_match_the_spec_registry_exactly() -> None:
    """Every registered markdown spec has a format doc, and vice versa.

    Guards against a future spec silently escaping format-doc/example
    validation because the hardcoded tuple was not extended.
    """
    import_module("ralph.mcp.artifacts.markdown.specs")
    registered = {spec.artifact_type for spec in registered_specs()}
    assert set(FORMAT_DOC_ARTIFACT_TYPES) == registered


def test_unknown_types_have_no_bundled_doc_or_example() -> None:
    assert load_bundled_format_doc("bogus") is None
    assert load_bundled_example("bogus") is None


def test_workspace_paths_are_canonical_markdown_paths() -> None:
    assert format_doc_workspace_path("plan") == ".agent/artifact-formats/plan.md"
    assert example_workspace_path("plan") == ".agent/artifact-formats/examples/plan.md"
    assert format_index_workspace_path() == (".agent/artifact-formats/artifact_formats_index.md")


def test_materialize_format_doc_and_example_round_trip() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    doc_path = materialize_format_doc(workspace_root, "commit_message", backend=backend)
    example_path = materialize_example(workspace_root, "commit_message", backend=backend)

    assert doc_path == format_doc_workspace_path("commit_message")
    assert example_path == example_workspace_path("commit_message")
    assert backend.read_text(workspace_root / doc_path) == load_bundled_format_doc("commit_message")
    assert backend.read_text(workspace_root / example_path) == load_bundled_example(
        "commit_message"
    )


def test_materialization_is_idempotent() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    first = materialize_all_format_docs(workspace_root, backend=backend)
    snapshot = dict(backend._files)
    second = materialize_all_format_docs(workspace_root, backend=backend)

    assert first == second
    assert backend._files == snapshot


def test_materialize_all_includes_docs_examples_and_index() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    paths = materialize_all_format_docs(workspace_root, backend=backend)

    expected = {
        *(format_doc_workspace_path(item) for item in FORMAT_DOC_ARTIFACT_TYPES),
        *(example_workspace_path(item) for item in EXAMPLE_ARTIFACT_TYPES),
        format_index_workspace_path(),
    }
    assert set(paths) == expected
    assert all(backend.exists(workspace_root / path) for path in expected)


def test_materialized_surface_regression_stale_docs_match_bundled_markdown(
    materialized_format_doc_contents: dict[str, str],
) -> None:
    """Regression: every agent-facing generated file must match its bundled source."""
    expected_content = {
        **{
            format_doc_workspace_path(item): load_bundled_format_doc(item)
            for item in FORMAT_DOC_ARTIFACT_TYPES
        },
        **{
            example_workspace_path(item): load_bundled_example(item)
            for item in EXAMPLE_ARTIFACT_TYPES
        },
        format_index_workspace_path(): load_bundled_format_index(),
    }

    for relative_path, bundled_content in expected_content.items():
        assert bundled_content is not None
        assert materialized_format_doc_contents[relative_path] == bundled_content


def test_materialize_unknown_type_has_no_side_effect() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    assert materialize_format_doc(workspace_root, "bogus", backend=backend) is None
    assert materialize_example(workspace_root, "bogus", backend=backend) is None
    assert backend._files == {}


def test_format_index_lists_every_supported_type_and_submission_tools() -> None:
    index = load_bundled_format_index()
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        assert artifact_type in index
    assert "ralph_submit_md_artifact" in index
    assert "ralph_verify_md_artifact" in index
    assert "ralph_submit_artifact" not in index


def test_materialize_index_round_trips_bundled_content() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    relative_path = materialize_format_index(workspace_root, backend=backend)

    assert relative_path == format_index_workspace_path()
    assert backend.read_text(workspace_root / relative_path) == load_bundled_format_index()


def test_docs_do_not_advertise_retired_json_submission_tools() -> None:
    retired = (
        "ralph_submit_artifact",
        "ralph_submit_plan_section",
        "ralph_submit_plan_sections",
        "ralph_validate_draft",
        "ralph_patch_step",
    )
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        doc = load_bundled_format_doc(artifact_type)
        assert doc is not None
        assert not any(tool in doc for tool in retired), (
            f"{artifact_type} advertises a retired artifact tool"
        )


def test_plan_doc_teaches_recommended_outline_without_requiring_a_skeleton() -> None:
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    for phrase in (
        "strongly recommended",
        "optional",
        "repeatable",
        "any order",
        "separate subplans",
        "nested mini-plans",
        "globally unique",
        "resolvable",
        "evaluatable",
        "Tiny task: compact checklist",
        "Medium task: conventional linear plan",
        "Large task: five-work-unit fan-out",
        "explicit fan-in integration and verification",
    ):
        assert phrase in doc
    assert doc.count("```markdown") >= 3
    assert "Required sections:" not in doc
    assert "closed shapes" not in doc


@pytest.mark.parametrize(
    ("artifact_type", "accepted_values"),
    _CLOSED_STATUS_VOCABULARIES.items(),
)
def test_consumed_status_docs_teach_closed_vocabulary(
    artifact_type: str,
    accepted_values: tuple[str, ...],
) -> None:
    doc = load_bundled_format_doc(artifact_type)
    assert doc is not None
    assert "hard error" in doc.lower()
    assert "`done`" in doc
    assert "`wrong`" in doc
    assert "coerc" not in doc.lower()
    for value in accepted_values:
        assert f"`{value}`" in doc


def test_commit_message_doc_teaches_closed_type_vocabulary() -> None:
    doc = load_bundled_format_doc("commit_message")
    assert doc is not None
    assert "hard error" in doc.lower()
    assert "`done`" in doc
    assert "`wrong`" in doc
    assert "`commit`" in doc
    assert "`skip`" in doc
    assert "coerc" not in doc.lower()
