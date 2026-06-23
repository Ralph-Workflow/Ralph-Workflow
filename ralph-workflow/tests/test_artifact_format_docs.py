"""Tests for ralph/mcp/artifacts/format_docs.py — bundled format doc module."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.artifacts.commit_message import normalize_commit_message_content
from ralph.mcp.artifacts.development_result import normalize_development_result_content
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
from ralph.mcp.artifacts.plan import normalize_plan_artifact_content
from ralph.mcp.artifacts.product_spec import normalize_product_spec_content
from ralph.mcp.artifacts.smoke_test_result import normalize_smoke_test_result_content
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_cleanup_content
from ralph.mcp.tools.artifact import handle_submit_artifact
from ralph.mcp.tools.coordination import InvalidParamsError
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_session import MockSession
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

MIN_CHECKLIST_BULLETS = 3


def test_module_contains_no_class_definitions() -> None:
    module = Path(__file__)
    syntax_tree = ast.parse(module.read_text(encoding="utf-8"))
    class_nodes = [node for node in syntax_tree.body if isinstance(node, ast.ClassDef)]
    assert class_nodes == []


def _extract_complete_example_inner_payload(doc: str) -> dict[str, object]:
    parts = doc.split("## Complete example")
    assert len(parts) > 1, "Missing '## Complete example' section"
    section = parts[1]
    match = re.search(r"```json\n(.*?)```", section, re.DOTALL)
    assert match is not None, "No ```json block in '## Complete example' section"
    outer = json.loads(match.group(1))
    assert isinstance(outer, dict)
    if "content" not in outer:
        return cast("dict[str, object]", outer)
    inner = json.loads(cast("str", outer["content"]))
    assert isinstance(inner, dict)
    return cast("dict[str, object]", inner)


def _extract_json_block_after_heading(doc: str, heading: str) -> dict[str, object]:
    parts = doc.split(heading, maxsplit=1)
    assert len(parts) == 2, f"Missing heading {heading!r}"
    match = re.search(r"```json\n(.*?)```", parts[1], re.DOTALL)
    assert match is not None, f"No ```json block after {heading!r}"
    payload = json.loads(match.group(1))
    assert isinstance(payload, dict)
    return cast("dict[str, object]", payload)


def _extract_fenced_json_blocks(markdown: str) -> list[str]:
    return re.findall(r"```json\s*\n(.*?)\n```", markdown, flags=re.DOTALL)


def test_all_supported_artifact_types_have_bundled_markdown() -> None:
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        doc = load_bundled_format_doc(artifact_type)
        assert doc is not None, f"No bundled doc for {artifact_type!r}"
        assert len(doc) > 0, f"Bundled doc for {artifact_type!r} is empty"
        assert "artifact format" in doc, (
            f"Bundled doc for {artifact_type!r} missing '# ... artifact format' heading"
        )


def test_planning_markdown_json_fences_are_parseable() -> None:
    """Planning-facing JSON examples must be real JSON, not pseudo-JSON."""
    repo_root = Path(__file__).resolve()
    package_root: Path | None = None
    for parent in repo_root.parents:
        if (parent / "pyproject.toml").exists():
            package_root = parent
            break
    assert package_root is not None
    paths = [
        package_root / "ralph" / "mcp" / "artifacts" / "format_docs" / "plan.md",
        package_root / "ralph" / "skills" / "content" / "submit-plan-artifact.md",
        package_root / "ralph" / "skills" / "content" / "submit-plan-step-edits.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        blocks = _extract_fenced_json_blocks(text)
        assert blocks, f"{path} has no fenced JSON blocks"
        for index, block in enumerate(blocks, start=1):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:  # pragma: no cover - failure path
                pytest.fail(f"{path}:{index} has invalid fenced JSON: {exc}")


def test_load_bundled_format_doc_returns_none_for_unsupported_type() -> None:
    assert load_bundled_format_doc("bogus") is None
    assert load_bundled_format_doc("") is None


def test_plan_format_doc_is_registered() -> None:
    assert "plan" in FORMAT_DOC_ARTIFACT_TYPES
    assert format_doc_workspace_path("plan") == ".agent/artifact-formats/plan.md"

    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert doc.startswith("# plan artifact format")

    for section in REQUIRED_SECTIONS:
        assert section in doc, f"plan format doc missing section: {section}"
    assert "## Dumb-proof checklist" in doc

    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")
    relative_path = materialize_format_doc(workspace_root, "plan", backend=backend)
    assert relative_path == ".agent/artifact-formats/plan.md"
    materialized = backend.read_text(workspace_root / relative_path)
    assert materialized == doc

    inner_payload = _extract_complete_example_inner_payload(doc)
    assert "summary" in inner_payload
    assert "skills_mcp" in inner_payload
    assert "steps" in inner_payload
    assert "critical_files" in inner_payload
    assert "risks_mitigations" in inner_payload
    assert "verification_strategy" in inner_payload
    scope_items = cast("list[dict[str, object]]", inner_payload["summary"]["scope_items"])
    assert len(scope_items) >= 3


def test_plan_format_doc_documents_lenient_staging_contract() -> None:
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "validation_warnings" in doc
    assert "Schema-invalid but valid JSON is staged" in doc
    for stale_fragment in (
        "validates ALL of them BEFORE any merge",
        "insert: range error",
        "silently dropped",
    ):
        assert stale_fragment not in doc


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

    assert materialize_format_doc(workspace_root, "bogus", backend=backend) is None
    assert not any(str(p).endswith("bogus.md") for p in backend._files)


def test_materialize_all_format_docs_materializes_every_supported_type() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual-ws")

    paths = materialize_all_format_docs(workspace_root, backend=backend)

    # Dynamic count: len(FORMAT_DOC_ARTIFACT_TYPES) per-type docs + 1 index doc
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
        "smoke_test_result": normalize_smoke_test_result_content,
        "commit_cleanup": normalize_commit_cleanup_content,
        "product_spec": normalize_product_spec_content,
    }
    passthrough_types = {
        "issues",
        "fix_result",
        "development_analysis_decision",
        "planning_analysis_decision",
        "review_analysis_decision",
        "plan",
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
        "development_analysis_decision": [
            "status",
            "summary",
            "what_came_up_short",
            "how_to_fix",
        ],
        "planning_analysis_decision": [
            "status",
            "summary",
            "what_came_up_short",
            "how_to_fix",
        ],
        "review_analysis_decision": [
            "status",
            "summary",
            "what_came_up_short",
            "how_to_fix",
        ],
        "smoke_test_result": [
            "status",
            "summary",
            "output_file",
            "observed_working",
            "observed_breaks",
            "headless_guide_checks",
        ],
        "commit_cleanup": [
            "analysis_complete",
            "actions",
            "action",
            "reason",
        ],
        "product_spec": [
            "title",
            "scope",
            "goals",
            "users",
            "success_criteria",
        ],
        "plan": [
            "summary",
            "skills_mcp",
            "steps",
            "critical_files",
            "risks_mitigations",
            "verification_strategy",
        ],
    }

    for artifact_type, fields in required_fields.items():
        doc = load_bundled_format_doc(artifact_type)
        assert doc is not None
        for field in fields:
            assert field in doc, f"Doc for {artifact_type!r} missing required field name {field!r}"


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
    doc_path = tmp_path / artifact_type / ".agent" / "artifact-formats" / f"{artifact_type}.md"
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
    # All analysis decision docs should be materialized
    assert (tmp_path / ".agent" / "artifact-formats" / "development_analysis_decision.md").exists()
    assert (tmp_path / ".agent" / "artifact-formats" / "planning_analysis_decision.md").exists()
    assert (tmp_path / ".agent" / "artifact-formats" / "review_analysis_decision.md").exists()
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
    assert len(bullet_lines) >= MIN_CHECKLIST_BULLETS, (
        f"Doc {artifact_type!r} checklist has fewer than {MIN_CHECKLIST_BULLETS} bullets"
    )


# ---------------------------------------------------------------------------
# Step 8: regression lock for the format doc content
# ---------------------------------------------------------------------------


def test_format_doc_forbids_test_step_type() -> None:
    """The bundled plan.md forbids step_type='test' and shows canonical field examples."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert 'Do NOT use `step_type: "test"`' in doc
    assert "Canonical field examples" in doc


# ---------------------------------------------------------------------------
# Step 2: new format doc sections (CoverageArea reference, Planning for any
# coding project, Planning quality guidance)
# Step 3: per-profile SE-bias defaults + Flexibility boundaries
# Step 4: Step-wise quickstart subheading + Worked example subheading
# ---------------------------------------------------------------------------


def test_detailed_bugfix_example_round_trips_and_links_analysis_fields(tmp_path: Path) -> None:
    """The first complete plan example is detailed enough to validate and analyze."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    payload = _extract_complete_example_inner_payload(doc)
    result = handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "plan",
            "content": json.dumps(payload),
        },
    )
    assert result.is_error is False, f"Detailed bugfix example did not round-trip: {result!r}"
    skills_mcp = cast("dict[str, object]", payload["skills_mcp"])
    assert skills_mcp["skills"] == ["test-driven-development", "systematic-debugging"]
    design = cast("dict[str, object]", payload["design"])
    acceptance_criteria = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", acceptance_criteria["criteria"])
    assert criteria


def test_format_doc_does_not_advertise_minimal_plan_or_atomic_path() -> None:
    """The bundled plan.md does not reintroduce minimal/atomic plan shortcuts."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    forbidden = (
        "## Minimal preset quickstart",
        'planning_profile="minimal"',
        '"minimal"',
        "Cheap-model",
        "cheap-model",
        "Atomic path",
        "short plan",
        "artifact_type=\"plan\"",
    )
    for text in forbidden:
        assert text not in doc


def test_format_doc_has_coverage_area_reference_section() -> None:
    """The bundled plan.md has a '## CoverageArea reference' section listing all 10 values."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## CoverageArea reference" in doc
    for value in (
        "bugfix",
        "feature",
        "refactor",
        "test",
        "docs",
        "infra",
        "security",
        "performance",
        "migration",
        "release",
    ):
        assert value in doc, f"CoverageArea value {value!r} missing from doc"


def test_format_doc_has_planning_for_any_coding_project_section() -> None:
    """The bundled plan.md has a project-shape section with balanced/strict profiles."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Planning for any coding project" in doc
    match = re.search(
        r"## Planning for any coding project.*?balanced.*?strict",
        doc,
        re.DOTALL,
    )
    assert match is not None
    assert "minimal" not in doc


def test_format_doc_has_planning_quality_guidance_section() -> None:
    """The bundled plan.md has planning-analysis quality guidance."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Planning quality guidance" in doc
    assert "Analysis-ready plan criteria" in doc
    assert "Every requirement from the prompt maps" in doc


def test_format_doc_preserves_se_opinionated_surfaces_section() -> None:
    """The bundled plan.md preserves the '## SE-opinionated design surfaces' section
    and gains the '### Profile-by-profile SE-bias defaults' sub-heading."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## SE-opinionated design surfaces" in doc
    assert "### Profile-by-profile SE-bias defaults" in doc
    assert "dead_code_policy" in doc


def test_format_doc_has_flexibility_boundaries_section() -> None:
    """The bundled plan.md has a '## Flexibility boundaries' section."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Flexibility boundaries" in doc
    assert "Does NOT fit" in doc
    assert "commit message" in doc or "issues.md" in doc


def test_format_doc_has_step_wise_quickstart_and_worked_example_subheadings() -> None:
    """The bundled plan.md has both step-wise sub-headings under '## Step-wise submission'."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "### Step-wise quickstart" in doc
    assert "### Worked example: staged section submission" in doc


# ---------------------------------------------------------------------------
# Step 12: size-limits, model-tier, project shape coverage
# ---------------------------------------------------------------------------


def test_plan_format_doc_mentions_size_limits_table() -> None:
    """The bundled plan.md has a '## Plan size limits' section with the cap table."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Plan size limits" in doc
    assert "max_total_bytes" in doc
    assert "max_steps" in doc
    assert "max_scope_items" in doc
    assert "max_acceptance_criteria" in doc
    assert "max_evidence_per_step" in doc
    # The byte value appears in one of the two accepted formats
    assert ("4_000_000" in doc) or ("4,000,000" in doc)


def test_plan_format_doc_has_closed_enums_section() -> None:
    """The bundled plan.md has a '## Closed enums' section enumerating all closed enums."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Closed enums" in doc
    # Spot-check the section enumerates the major closed enums
    for enum_label in (
        "`StepType`",
        "`ScopeCategory`",
        "`CoverageArea`",
        "`intent_verb`",
        "`StepTarget.action`",
        "`DriftDetection.sources`",
        "`DriftDetection.on_drift_action`",
        "`RefactorStrategy.approach`",
        "`RefactorStrategy.dead_code_policy`",
        "`Testability.forbidden_in_tests`",
        "`Testability.required_test_layers`",
        "`DependencyInjection.preferred_patterns`",
        "`DependencyInjection.forbidden_patterns`",
        "`DesignConstraints.architecture_style`",
    ):
        assert enum_label in doc, f"Closed enums section missing {enum_label}"


def test_plan_format_doc_has_high_quality_model_example() -> None:
    """The bundled plan.md has a detailed architecture example."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Complete example (detailed architecture plan)" in doc
    # The example populates design.notes (>=500 chars rationale) and drift_detection.guard_commands
    assert "design.notes" in doc
    assert "drift_detection.guard_commands" in doc
    # The example uses a diamond depends_on graph
    assert "1 -> 2" in doc or "1->2" in doc
    # The example uses the typed EvidenceRef shape
    assert '"kind"' in doc and '"ref"' in doc


def test_plan_format_doc_detailed_architecture_example_validates_with_real_plan_logic() -> None:
    """The later detailed architecture example must validate, not just exist as prose."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    payload = _extract_json_block_after_heading(
        doc, "## Complete example (detailed architecture plan)"
    )

    normalized = normalize_plan_artifact_content(payload)
    design = cast("dict[str, object]", normalized["design"])
    criteria = cast(
        "list[dict[str, object]]",
        cast("dict[str, object]", design["acceptance_criteria"])["criteria"],
    )
    assert all(
        4 not in cast("list[int]", criterion.get("satisfied_by_steps", []))
        for criterion in criteria
    )
    assert criteria[2]["satisfied_by_steps"] == [2]


def test_plan_format_doc_preserves_audit_literals() -> None:
    """The bundled plan.md contains the audit literals."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "agent-managed sub-agents" in doc, (
        "audit_parallelization_dormant requires the literal 'agent-managed sub-agents'"
    )
    assert "fan-out is dormant" in doc, (
        "audit_parallelization_dormant requires the literal 'fan-out is dormant'"
    )


def test_plan_format_doc_documents_step_mutation_echo_payload() -> None:
    """The bundled plan.md documents the read-after-write echo payload."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "Step-mutation read-after-write echo" in doc
    assert "reindex_map" in doc
    assert "rewritten_depends_on" in doc
    assert "rewritten_ac_satisfied_by_steps" in doc
    assert "dropped_ac_satisfied_by_steps" in doc


def test_plan_format_doc_documents_three_new_tools() -> None:
    """The bundled plan.md documents the three new MCP tools."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "ralph_patch_step" in doc
    assert "ralph_validate_draft" in doc
    assert "ralph_submit_plan_sections" in doc


def test_plan_format_doc_high_quality_example_does_not_use_verify_steps_for_ac_satisfaction(
) -> None:
    """The high-quality example must not contradict the AC satisfied_by_steps validator."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert '"title": "Run ruff + mypy + pytest"' in doc
    assert '"step_type": "verify"' in doc
    assert '"satisfied_by_steps": [4]' not in doc
    assert '"satisfied_by_steps": [2, 3]' in doc


def test_plan_format_doc_step_type_reference_uses_file_change_for_test_authoring() -> None:
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert '"title": "Add a regression test"' in doc
    assert '"targets": [{"path": "tests/test_foo.py", "action": "modify"}]' in doc


def test_plan_format_doc_documents_step_type_alias_coercion() -> None:
    """The bundled plan.md documents canonical step type values, not hidden aliases."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "StepType aliases" in doc
    assert "_coerce_step_type_aliases" not in doc


def test_plan_format_doc_did_not_add_duplicate_h2() -> None:
    """The bundled plan.md does NOT add the duplicate-H2 names from the prior revision.

    Guards against regressions of the bug where '## Model tier guidance' (no hyphen)
    and '## Universal project coverage' were added as siblings of the existing
    '## Model-tier guidance' and '## Planning for any coding project' sections.
    """
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Model tier guidance" not in doc, (
        "Duplicate H2 '## Model tier guidance' (no hyphen) must NOT be added"
    )
    assert "## Universal project coverage" not in doc, (
        "Duplicate H2 '## Universal project coverage' must NOT be added"
    )


def test_plan_format_doc_h2_count_increased_by_one() -> None:
    """The bundled plan.md H2 count reflects the current canonical sections."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    h2_count = sum(1 for line in doc.split("\n") if line.startswith("## "))
    assert h2_count == 25, f"Expected exactly 25 H2 sections in plan format doc, got {h2_count}"


def test_plan_format_doc_has_canonical_validator_errors_to_fix_section() -> None:
    """The bundled plan.md has a '## Canonical validator errors to fix' section.

    Regression lock: this section must exist BETWEEN '## Dumb-proof checklist'
    and '## Module family' and must enumerate the cross-section validator
    error strings emitted by ralph/mcp/artifacts/plan/_validation.py.
    """
    doc = load_bundled_format_doc("plan")
    assert doc is not None

    dumb_proof_index = doc.find("## Dumb-proof checklist")
    canonical_index = doc.find("## Canonical validator errors to fix")
    module_family_index = doc.find("## Module family")

    assert dumb_proof_index >= 0, "plan format doc missing '## Dumb-proof checklist' section"
    assert canonical_index >= 0, (
        "plan format doc missing '## Canonical validator errors to fix' section"
    )
    assert module_family_index >= 0, "plan format doc missing '## Module family' section"

    assert dumb_proof_index < canonical_index < module_family_index, (
        "'## Canonical validator errors to fix' must appear between "
        "'## Dumb-proof checklist' and '## Module family'"
    )

    canonical_section = doc[canonical_index:module_family_index]
    canonical_error_strings = (
        "plan step depends_on cycle detected at step N",
        "plan cannot declare both parallel_plan and work_units; pick one",
        "verification method must not invoke a shell interpreter directly",
        "satisfied_by_steps cannot reference a research or verify step",
        "skills_mcp.skills must contain at least one skill name",
        "plan envelope has no valid",
        "plan payload must decode to a JSON object",
        "plan draft is missing a 'sections' object",
    )
    for needle in canonical_error_strings:
        assert needle in canonical_section, (
            f"plan format doc '## Canonical validator errors to fix' missing literal: {needle!r}"
        )


def test_plan_format_doc_removed_model_tier_guidance() -> None:
    """The plan doc no longer teaches model-tier/minimal-plan shortcuts."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Model-tier guidance" not in doc
    assert "model tier" not in doc.lower()


def test_plan_format_doc_extends_existing_planning_for_any_coding_project() -> None:
    """The '## Planning for any coding project' section is extended in place
    with 'Project shape coverage' plus all 8 project shapes."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Planning for any coding project" in doc
    assert "Project shape coverage" in doc
    for shape in (
        "CLI",
        "Libraries",
        "Refactors",
        "Migrations",
        "Infra",
        "Security",
        "Performance",
        "Multi-stack",
    ):
        assert shape in doc, f"Project shape {shape!r} missing from doc"


def test_plan_format_doc_module_family_lists_size_limits() -> None:
    """The '## Module family' section lists '_size_limits' and 'PlanSizeLimits'."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Module family" in doc
    assert "_size_limits" in doc
    assert "PlanSizeLimits" in doc


def test_plan_format_doc_dumb_proof_checklist_has_size_limit_bullet() -> None:
    """The '## Dumb-proof checklist' section contains the new size-limit bullet."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Dumb-proof checklist" in doc
    assert "Did you verify the plan fits within the size limits" in doc
