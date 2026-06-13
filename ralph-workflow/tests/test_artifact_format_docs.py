"""Tests for ralph/mcp/artifacts/format_docs.py — bundled format doc module."""

from __future__ import annotations

import ast
import json
import re
import tempfile
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
    assert not any(
        str(p).endswith("bogus.md") for p in backend._files
    )


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
    """The bundled plan.md forbids step_type='test' and contains the cheap-model examples."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert 'Do NOT use `step_type: "test"`' in doc
    assert "Cheap-model shortcut examples" in doc


# ---------------------------------------------------------------------------
# Step 2: new format doc sections (Minimal preset quickstart, CoverageArea
# reference, Planning for any coding project, Model-tier guidance)
# Step 3: per-preset SE-bias defaults + Flexibility boundaries
# Step 4: Step-wise quickstart subheading + Worked example subheading
# ---------------------------------------------------------------------------


def test_minimal_preset_quickstart_example_round_trips() -> None:
    """The fenced JSON in '## Minimal preset quickstart' round-trips."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    parts = doc.split("## Minimal preset quickstart")
    assert len(parts) > 1, "Missing '## Minimal preset quickstart' section"
    section = parts[1]
    match = re.search(r"```json\n(.*?)```", section, re.DOTALL)
    assert match is not None, "No ```json block in '## Minimal preset quickstart' section"
    inner_payload = json.loads(match.group(1))
    assert isinstance(inner_payload, dict)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        result = handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "plan",
                "content": json.dumps(inner_payload),
            },
        )
        assert result.is_error is False, (
            f"Minimal preset quickstart did not round-trip: {result!r}"
        )


def test_format_doc_has_minimal_preset_quickstart_section() -> None:
    """The bundled plan.md has a '## Minimal preset quickstart' section."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Minimal preset quickstart" in doc
    assert "design.planning_profile" in doc
    assert "```json" in doc


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
    """The bundled plan.md has a '## Planning for any coding project' section with all 3 presets."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Planning for any coding project" in doc
    match = re.search(
        r"## Planning for any coding project.*?minimal.*?balanced.*?strict",
        doc,
        re.DOTALL,
    )
    assert match is not None, (
        "## Planning for any coding project section is missing one of the 3 preset names"
    )


def test_format_doc_has_model_tier_guidance_section() -> None:
    """The bundled plan.md has a '## Model-tier guidance' section with both tiers."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Model-tier guidance" in doc
    assert "Cheap-model" in doc
    assert "High-quality-model" in doc


def test_format_doc_preserves_se_opinionated_surfaces_section() -> None:
    """The bundled plan.md preserves the '## SE-opinionated design surfaces' section
    and gains the '### Preset-by-preset SE-bias defaults' sub-heading."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## SE-opinionated design surfaces" in doc
    assert "### Preset-by-preset SE-bias defaults" in doc
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
    assert "### Step-wise quickstart (cheap-model baseline)" in doc
    assert "### Worked example: 3-section short plan" in doc


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
    """The bundled plan.md H2 count is exactly 23 (was 22, gained 1)."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    h2_count = sum(1 for line in doc.split("\n") if line.startswith("## "))
    assert h2_count == 23, (
        f"Expected exactly 23 H2 sections in plan format doc, got {h2_count}"
    )


def test_plan_format_doc_extends_existing_model_tier_guidance() -> None:
    """The '## Model-tier guidance' section is extended in place with 'Size-cap awareness'."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Model-tier guidance" in doc
    # The new H4 subsection added in place
    assert "Size-cap awareness" in doc


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
