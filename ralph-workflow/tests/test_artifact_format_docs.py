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
from ralph.mcp.artifacts.markdown.specs import ANALYSIS_DECISION_SPECS
from tests._artifact_format_docs_memory_backend import MemoryBackend

_POLICY_REMEDIATION_ANALYSIS_DECISION = "policy_remediation_analysis_decision"
_ANALYSIS_DECISION_TYPES = tuple(spec.artifact_type for spec in ANALYSIS_DECISION_SPECS)
_CLOSED_STATUS_VOCABULARIES = {
    "issues": ("issues_found", "no_issues"),
    "development_result": ("completed", "partial", "failed"),
    "planning_analysis_decision": ("completed", "request_changes", "failed"),
    "development_analysis_decision": ("completed", "request_changes", "failed"),
    "review_analysis_decision": ("completed", "request_changes", "failed"),
    "policy_remediation_analysis_decision": ("completed", "request_changes", "failed"),
    "smoke_test_result": ("passed", "failed", "partial"),
}


@pytest.fixture
def materialized_format_doc_contents(tmp_path: Path) -> dict[str, str]:
    """Materialize bundled docs in an isolated workspace for the surface check."""
    materialize_all_format_docs(tmp_path)
    formats_root = tmp_path / ".agent" / "artifact-formats"
    return {
        str(path.relative_to(tmp_path)): path.read_text(encoding="utf-8")
        for path in formats_root.rglob("*.md")
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


@pytest.mark.parametrize("artifact_type", _ANALYSIS_DECISION_TYPES)
def test_analysis_format_docs_teach_evidence_first_decision_invariants(
    artifact_type: str,
) -> None:
    doc = load_bundled_format_doc(artifact_type)

    assert doc is not None
    normalized = " ".join(doc.split())
    if artifact_type != "review_analysis_decision":
        assert "## Criterion Verdicts" in normalized
        assert "Criterion:" in normalized
        assert "Expected observation:" in normalized
        assert "not permitted" in normalized
    if artifact_type != _POLICY_REMEDIATION_ANALYSIS_DECISION:
        assert "stable" in normalized


def test_development_analysis_example_uses_self_run_current_evidence() -> None:
    doc = load_bundled_format_doc("development_analysis_decision")

    assert doc is not None
    assert "was not executed" not in doc
    assert "Expected observation:" in doc
    assert "Evidence:" in doc
    assert "Run the exact pytest target for the parser and record the output." not in doc


def test_policy_remediation_inline_example_uses_a_localized_verdict() -> None:
    doc = load_bundled_format_doc(_POLICY_REMEDIATION_ANALYSIS_DECISION)

    assert doc is not None
    assert doc.count("- [PR-001]") == 3
    assert "Verdict: not met" in doc
    assert "## Criterion Verdicts" in doc
    assert "not permitted" in doc


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


def test_plan_doc_teaches_mandatory_executor_ready_contract() -> None:
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    for phrase in (
        "Every active plan uses stable `### [S-n] Title` steps",
        "Work steps require `Files`, a concrete `Verify`, and an observable `Expect`.",
        "`schema_version` and `## Validation Overrides` are unsupported",
        "Orient, Characterize, Change, and Verify",
        "ralph_edit_md_artifact",
        ".agent/artifact-formats/examples/plan.md",
    ):
        assert phrase in doc
    assert doc.count("```markdown") == 1
    assert "`PLAN001` is the sole error" not in doc
    assert "Warnings and info never make a plan invalid" not in doc


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
    if artifact_type.endswith("analysis_decision") and artifact_type != "review_analysis_decision":
        assert "not evaluable" in doc
    else:
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


def test_format_docs_teach_tolerant_descriptive_extensions() -> None:
    index = load_bundled_format_index()
    assert "Unknown descriptive frontmatter fields and sections are accepted" in index
    assert "unrecognized vocabulary choices such as a status" not in index

    for artifact_type in ("commit_message", "product_spec", "fix_result"):
        doc = load_bundled_format_doc(artifact_type)
        assert doc is not None
        assert "Unknown descriptive frontmatter fields and sections are accepted" in doc
        assert "unknown sections" not in doc.lower()
