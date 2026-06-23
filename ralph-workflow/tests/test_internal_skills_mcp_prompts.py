"""Regression tests for the new internal skills (submit-plan-artifact, submit-artifact)
and the skill pointers added to the planning prompt family and MCP error helpers.

This file is pure-Python: it only reads source files via ``Path.read_text`` and
directly invokes the seven target error helpers against a real
``PathFileBackend`` rooted in ``tmp_path``. No subprocess, no ``time.sleep``,
no real network. The new ``test_internal_skills_mcp_prompts`` module is
expected to run in well under a second so the 60 s combined test budget
remains GREEN.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jinja2 import ChainableUndefined, DictLoader, Environment

from ralph.mcp.artifacts._path_file_backend import PathFileBackend
from ralph.mcp.tools.artifact import (
    _artifact_content_format_error,
    _format_plan_batch_envelope_error,
    _format_plan_finalize_error,
    _format_plan_section_submission_error,
    _format_plan_step_edit_error,
    _raise_format_doc_error,
    _raise_index_format_error,
)
from ralph.skills._content import BASELINE_SKILL_NAMES
from tests.test_prompt_template_files import (
    PLANNING_ANALYSIS_CORE_WORKFLOW_GUIDANCE,
    PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE,
    PLANNING_EDIT_ADJACENT_ISSUES_GUIDANCE,
    PLANNING_EDIT_CLOSURE_LEDGER_GUIDANCE,
    PLANNING_EDIT_FALLBACK_HISTORY_GUIDANCE,
    PLANNING_EDIT_FALLBACK_SCOPE_CONDITIONAL_GUIDANCE,
    PLANNING_EDIT_FALLBACK_SCOUT_GUIDANCE,
    PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE,
    PLANNING_STABLE_ID_GUIDANCE,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "ralph" / "skills" / "content"
TEMPLATES_DIR = REPO_ROOT / "ralph" / "prompts" / "templates"

PLAN_SKILL_PATH = SKILL_DIR / "submit-plan-artifact.md"
PLAN_STEP_EDITS_SKILL_PATH = SKILL_DIR / "submit-plan-step-edits.md"
ARTIFACT_SKILL_PATH = SKILL_DIR / "submit-artifact.md"
COMMIT_MESSAGE_SKILL_PATH = SKILL_DIR / "submit-commit-message-artifact.md"
DEVELOPMENT_RESULT_SKILL_PATH = SKILL_DIR / "submit-development-result-artifact.md"
COMMIT_CLEANUP_SKILL_PATH = SKILL_DIR / "submit-commit-cleanup-artifact.md"
PLANNING_JINJA = TEMPLATES_DIR / "planning.jinja"
PLANNING_FALLBACK_JINJA = TEMPLATES_DIR / "planning_fallback.jinja"
PLANNING_EDIT_JINJA = TEMPLATES_DIR / "planning_edit.jinja"
PLANNING_EDIT_FALLBACK_JINJA = TEMPLATES_DIR / "planning_edit_fallback.jinja"
COMMIT_MESSAGE_JINJA = TEMPLATES_DIR / "commit_message.jinja"
DEVELOPER_ITERATION_JINJA = TEMPLATES_DIR / "developer_iteration.jinja"
DEVELOPER_ITERATION_CONTINUATION_JINJA = TEMPLATES_DIR / "developer_iteration_continuation.jinja"
COMMIT_CLEANUP_JINJA = TEMPLATES_DIR / "commit_cleanup.jinja"
DEVELOPMENT_ANALYSIS_JINJA = TEMPLATES_DIR / "development_analysis.jinja"
REVIEW_ANALYSIS_JINJA = TEMPLATES_DIR / "review_analysis.jinja"
PLANNING_ANALYSIS_JINJA = TEMPLATES_DIR / "planning_analysis.jinja"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"---\n(?P<fm>.*?)\n---\n(?P<body>.*)", text, re.DOTALL)
    if match is None:
        raise AssertionError(f"missing YAML frontmatter: {text[:80]!r}")
    fm_raw = match.group("fm")
    body = match.group("body")
    fields: dict[str, str] = {}
    for line in fm_raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, body


def _read_skill(path: Path) -> tuple[dict[str, str], str]:
    return _parse_frontmatter(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC-01 — submit-plan-artifact.md skill shape
# ---------------------------------------------------------------------------


def test_submit_plan_artifact_skill_shape() -> None:
    assert PLAN_SKILL_PATH.exists(), f"missing skill: {PLAN_SKILL_PATH}"
    fm, body = _read_skill(PLAN_SKILL_PATH)

    assert fm.get("name") == "submit-plan-artifact"
    assert fm.get("description"), "frontmatter description is required"
    assert fm["description"].startswith("Use when"), (
        f"description must start with 'Use when', got: {fm['description']!r}"
    )

    expected_h2 = (
        "## Overview",
        "## When to Use",
        "## Core Flow",
        "## Planning Quality Criteria",
        "## Correcting a Rejected Payload",
        "## Source of Truth Reference",
        "## Common Mistakes",
    )
    for header in expected_h2:
        assert header in body, f"missing H2 section: {header!r}"

    assert "optional" in body.lower(), "body must explicitly mark the skill as optional"
    assert ".agent/artifact-formats/plan.md" in body, "body must reference the plan format doc"
    assert "ralph_submit_plan_section" in body, "body must mention ralph_submit_plan_section"
    assert "retry" in body.lower(), "body must contain retry guidance"
    assert "ralph_submit_artifact" in body, (
        "body must differentiate from the ralph_submit_artifact MCP tool"
    )


def test_submit_plan_artifact_skill_blocks_stale_atomic_and_minimal_guidance() -> None:
    """The authored skill must not reintroduce invalid plan recovery shortcuts."""
    _, body = _read_skill(PLAN_SKILL_PATH)

    forbidden_fragments = (
        "The atomic `ralph_submit_artifact` payload",
        '"artifact_type": "plan"',
        'design.planning_profile = "minimal"',
        'planning_profile = "minimal"',
        "to permit an empty list",
        "empty skill lists are allowed",
    )
    for fragment in forbidden_fragments:
        assert fragment not in body, (
            f"submit-plan-artifact.md contains stale/minimal plan guidance: {fragment!r}"
        )

    assert "Do not retry plan submission through generic `ralph_submit_artifact`" in body
    assert "Empty skill lists are invalid for every planning profile" in body


# ---------------------------------------------------------------------------
# AC-02 — submit-artifact.md skill shape
# ---------------------------------------------------------------------------


def test_submit_artifact_skill_shape() -> None:
    assert ARTIFACT_SKILL_PATH.exists(), f"missing skill: {ARTIFACT_SKILL_PATH}"
    fm, body = _read_skill(ARTIFACT_SKILL_PATH)

    assert fm.get("name") == "submit-artifact"
    assert fm.get("description"), "frontmatter description is required"
    assert fm["description"].startswith("Use when"), (
        f"description must start with 'Use when', got: {fm['description']!r}"
    )

    expected_h2 = (
        "## Overview",
        "## When to Use",
        "## Core Flow (canonical submission)",
        "## Recovery from a Bad Payload",
        "## Source of Truth Reference",
        "## Common Mistakes",
    )
    for header in expected_h2:
        assert header in body, f"missing H2 section: {header!r}"

    assert "optional" in body.lower(), "body must explicitly mark the skill as optional"
    assert ".agent/artifact-formats/artifact_formats_index.md" in body, (
        "body must reference the artifact formats index doc"
    )
    assert "artifact_type" in body and "content" in body, (
        "body must show artifact_type and content envelope keys"
    )
    assert "retry" in body.lower(), "body must contain retry guidance"
    assert "ralph_submit_artifact" in body, (
        "body must mention the ralph_submit_artifact MCP tool"
    )


# ---------------------------------------------------------------------------
# AC-03 — BASELINE_SKILL_NAMES registers both new skills (length 30)
# ---------------------------------------------------------------------------


def test_baseline_skill_names_includes_new_skills() -> None:
    assert isinstance(BASELINE_SKILL_NAMES, tuple)
    assert len(BASELINE_SKILL_NAMES) == 30, (
        f"expected 30 baseline skills after registration, got {len(BASELINE_SKILL_NAMES)}"
    )
    assert "submit-plan-artifact" in BASELINE_SKILL_NAMES
    assert "submit-plan-step-edits" in BASELINE_SKILL_NAMES
    assert "submit-artifact" in BASELINE_SKILL_NAMES
    assert "submit-commit-message-artifact" in BASELINE_SKILL_NAMES
    assert "submit-development-result-artifact" in BASELINE_SKILL_NAMES
    assert "submit-commit-cleanup-artifact" in BASELINE_SKILL_NAMES


# ---------------------------------------------------------------------------
# AC-04 — planning.jinja gains a skill pointer without losing invariants
# ---------------------------------------------------------------------------


def test_planning_jinja_skill_pointer_and_invariants() -> None:
    source = PLANNING_JINJA.read_text(encoding="utf-8")
    assert "submit-plan-artifact" in source, "planning.jinja must reference submit-plan-artifact"
    preserved = (
        "## PROMPT SCOPE CLASSIFICATION",
        "Common StepType mistakes",
        "Plan-artifact scope (planner-meta-task)",
        "## Agent-Driven Parallel Execution",
        "Ralph-managed fan-out is dormant",
        "sub-agents",
        ".agent",
        ".git",
        "allowed_directories",
    )
    for needle in preserved:
        assert needle in source, f"planning.jinja must preserve {needle!r}"
    banned = (
        "## Same-Workspace Parallel Worker Rules",
        "ralph coordinate",
    )
    for needle in banned:
        assert needle not in source, f"planning.jinja must NOT contain {needle!r}"


# ---------------------------------------------------------------------------
# AC-05 — planning_fallback.jinja gains a skill pointer and preserves headings
# ---------------------------------------------------------------------------


def test_planning_fallback_jinja_skill_pointer_and_invariants() -> None:
    source = PLANNING_FALLBACK_JINJA.read_text(encoding="utf-8")
    assert "submit-plan-artifact" in source, (
        "planning_fallback.jinja must reference submit-plan-artifact"
    )
    preserved = (
        "## Plan-artifact canonical contract",
        "Plan size limits",
        "Cycle guard",
        "ARTIFACT_HISTORY_PATH",
        "ARTIFACT_HISTORY_DIR",
    )
    for needle in preserved:
        assert needle in source, f"planning_fallback.jinja must preserve {needle!r}"

    rendered = _render_template_source(PLANNING_FALLBACK_JINJA)
    heading_count = rendered.count("## OPTIONAL: submit-plan-artifact skill")
    assert heading_count == 1, (
        "planning_fallback.jinja must render exactly one "
        "'## OPTIONAL: submit-plan-artifact skill' heading (was "
        f"{heading_count}); the shared include already emits the heading, so "
        "the source must not duplicate it inline."
    )


# ---------------------------------------------------------------------------
# AC-05 — planning_edit.jinja and planning_edit_fallback.jinja gain a skill pointer
# ---------------------------------------------------------------------------


def test_planning_edit_and_fallback_skill_pointer() -> None:
    edit_source = PLANNING_EDIT_JINJA.read_text(encoding="utf-8")
    edit_fallback_source = PLANNING_EDIT_FALLBACK_JINJA.read_text(encoding="utf-8")

    assert "submit-plan-artifact" in edit_source, (
        "planning_edit.jinja must reference submit-plan-artifact"
    )
    assert "submit-plan-artifact" in edit_fallback_source, (
        "planning_edit_fallback.jinja must reference submit-plan-artifact"
    )

    for source, label in (
        (edit_source, "planning_edit.jinja"),
        (edit_fallback_source, "planning_edit_fallback.jinja"),
    ):
        for needle in (
            PLANNING_EDIT_CLOSURE_LEDGER_GUIDANCE,
            PLANNING_EDIT_ADJACENT_ISSUES_GUIDANCE,
            PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE,
            PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE,
            PLANNING_STABLE_ID_GUIDANCE,
        ):
            assert needle in source, f"{label} must preserve {needle!r}"

    for needle in (
        PLANNING_ANALYSIS_CORE_WORKFLOW_GUIDANCE,
        PLANNING_EDIT_FALLBACK_SCOUT_GUIDANCE,
        PLANNING_EDIT_FALLBACK_HISTORY_GUIDANCE,
        PLANNING_EDIT_FALLBACK_SCOPE_CONDITIONAL_GUIDANCE,
    ):
        assert needle in edit_fallback_source, (
            f"planning_edit_fallback.jinja must preserve {needle!r}"
        )

    for source, label in (
        (edit_source, "planning_edit.jinja"),
        (edit_fallback_source, "planning_edit_fallback.jinja"),
    ):
        for needle in ("ARTIFACT_HISTORY_PATH", "ARTIFACT_HISTORY_DIR"):
            assert needle in source, f"{label} must preserve {needle!r}"
        for needle in ("arabold/docs-mcp-server", "localhost:6280"):
            assert needle in source, f"{label} must preserve {needle!r}"


# ---------------------------------------------------------------------------
# AC-06 — Plan error helpers mention the submit-plan-artifact skill
# ---------------------------------------------------------------------------


def _call_plan_helper(
    helper_name: str,
    *,
    workspace_root: Path,
    backend: PathFileBackend,
) -> str:
    """Call one of the four plan error helpers with a synthetic detail."""
    detail = "synthetic test detail"

    if helper_name == "_format_plan_section_submission_error":
        return _format_plan_section_submission_error(
            section="summary",
            mode="replace",
            detail=detail,
            workspace_root=workspace_root,
            backend=backend,
            tool_name="ralph_submit_plan_section",
        )
    if helper_name == "_format_plan_batch_envelope_error":
        return _format_plan_batch_envelope_error(
            detail=detail,
            workspace_root=workspace_root,
            backend=backend,
        )
    if helper_name == "_format_plan_finalize_error":
        return _format_plan_finalize_error(
            detail=detail,
            workspace_root=workspace_root,
            backend=backend,
            tool_name="ralph_finalize_plan",
        )
    if helper_name == "_format_plan_step_edit_error":
        return _format_plan_step_edit_error(
            detail=detail,
            workspace_root=workspace_root,
            backend=backend,
            tool_name="ralph_insert_plan_step",
        )
    msg = f"unknown plan helper: {helper_name}"
    raise AssertionError(msg)


@pytest.mark.parametrize(
    "helper_name",
    [
        "_format_plan_section_submission_error",
        "_format_plan_batch_envelope_error",
        "_format_plan_finalize_error",
        "_format_plan_step_edit_error",
    ],
)
def test_plan_error_helpers_mention_submit_plan_artifact_skill(
    helper_name: str,
    tmp_path: Path,
) -> None:
    backend = PathFileBackend()
    workspace_root = tmp_path
    # _plan_format_doc_reference calls materialize_format_doc which writes a
    # file into the workspace; tmp_path satisfies the side effect without
    # polluting the real repo.
    message = _call_plan_helper(
        helper_name,
        workspace_root=workspace_root,
        backend=backend,
    )
    assert "submit-plan-artifact" in message, (
        f"{helper_name} must mention the submit-plan-artifact skill pointer"
    )
    assert ".agent/artifact-formats/plan.md" in message, (
        f"{helper_name} must keep the existing plan.md format-doc reference"
    )


# ---------------------------------------------------------------------------
# AC-06 — Generic artifact error helpers mention the submit-artifact skill
# ---------------------------------------------------------------------------


def test_raise_index_format_error_mentions_submit_artifact_skill(tmp_path: Path) -> None:
    backend = PathFileBackend()
    with pytest.raises(Exception) as excinfo:
        _raise_index_format_error(
            tmp_path,
            backend,
            "synthetic index error",
        )
    message = str(excinfo.value)
    assert "submit-artifact" in message, (
        "_raise_index_format_error must mention the submit-artifact skill pointer"
    )
    assert ".agent/artifact-formats/artifact_formats_index.md" in message, (
        "_raise_index_format_error must keep the existing index-doc reference"
    )


def test_raise_format_doc_error_mentions_submit_artifact_skill(tmp_path: Path) -> None:
    backend = PathFileBackend()
    exc = RuntimeError("synthetic format-doc error")
    with pytest.raises(Exception) as excinfo:
        _raise_format_doc_error("development_result", tmp_path, backend, exc)
    message = str(excinfo.value)
    assert "submit-artifact" in message, (
        "_raise_format_doc_error must mention the submit-artifact skill pointer"
    )
    assert ".agent/artifact-formats/development_result.md" in message, (
        "_raise_format_doc_error must keep the existing format-doc reference"
    )


def test_artifact_content_format_error_mentions_submit_artifact_skill() -> None:
    message = _artifact_content_format_error("commit_message")
    assert "submit-artifact" in message, (
        "_artifact_content_format_error must mention the submit-artifact skill pointer"
    )
    assert "content" in message and "artifact_type" in message, (
        "_artifact_content_format_error must keep the existing canonical envelope example"
    )


# ---------------------------------------------------------------------------
# AC-07 — Three new skill shape tests (commit_message, development_result, commit_cleanup)
# ---------------------------------------------------------------------------


def test_submit_commit_message_artifact_skill_shape() -> None:
    assert COMMIT_MESSAGE_SKILL_PATH.exists(), f"missing skill: {COMMIT_MESSAGE_SKILL_PATH}"
    fm, body = _read_skill(COMMIT_MESSAGE_SKILL_PATH)

    assert fm.get("name") == "submit-commit-message-artifact"
    assert fm.get("description"), "frontmatter description is required"
    assert fm["description"].startswith("Use when"), (
        f"description must start with 'Use when', got: {fm['description']!r}"
    )

    expected_h2 = (
        "## Overview",
        "## When to Use",
        "## Core Flow (one-shot)",
        "## Recovery from a Bad Payload",
        "## Source of Truth Reference",
        "## Common Mistakes",
    )
    for header in expected_h2:
        assert header in body, f"missing H2 section: {header!r}"

    assert "optional" in body.lower(), "body must explicitly mark the skill as optional"
    assert ".agent/artifact-formats/commit_message.md" in body, (
        "body must reference the commit_message format doc"
    )
    assert "retry" in body.lower(), "body must contain retry guidance"
    assert "ralph_submit_artifact" in body, (
        "body must mention the ralph_submit_artifact MCP tool"
    )


def test_submit_development_result_artifact_skill_shape() -> None:
    assert DEVELOPMENT_RESULT_SKILL_PATH.exists(), (
        f"missing skill: {DEVELOPMENT_RESULT_SKILL_PATH}"
    )
    fm, body = _read_skill(DEVELOPMENT_RESULT_SKILL_PATH)

    assert fm.get("name") == "submit-development-result-artifact"
    assert fm.get("description"), "frontmatter description is required"
    assert fm["description"].startswith("Use when"), (
        f"description must start with 'Use when', got: {fm['description']!r}"
    )

    expected_h2 = (
        "## Overview",
        "## When to Use",
        "## Core Flow (one-shot)",
        "## Recovery from a Bad Payload",
        "## Source of Truth Reference",
        "## Common Mistakes",
    )
    for header in expected_h2:
        assert header in body, f"missing H2 section: {header!r}"

    assert "optional" in body.lower(), "body must explicitly mark the skill as optional"
    assert ".agent/artifact-formats/development_result.md" in body, (
        "body must reference the development_result format doc"
    )
    assert "plan_items_proven" in body, "body must surface plan_items_proven field"
    assert "retry" in body.lower(), "body must contain retry guidance"
    assert "ralph_submit_artifact" in body, (
        "body must mention the ralph_submit_artifact MCP tool"
    )


def test_submit_commit_cleanup_artifact_skill_shape() -> None:
    assert COMMIT_CLEANUP_SKILL_PATH.exists(), f"missing skill: {COMMIT_CLEANUP_SKILL_PATH}"
    fm, body = _read_skill(COMMIT_CLEANUP_SKILL_PATH)

    assert fm.get("name") == "submit-commit-cleanup-artifact"
    assert fm.get("description"), "frontmatter description is required"
    assert fm["description"].startswith("Use when"), (
        f"description must start with 'Use when', got: {fm['description']!r}"
    )

    expected_h2 = (
        "## Overview",
        "## When to Use",
        "## Core Flow (one-shot)",
        "## Recovery from a Bad Payload",
        "## Source of Truth Reference",
        "## Common Mistakes",
        "## Red Flags - STOP and Start Over",
    )
    for header in expected_h2:
        assert header in body, f"missing H2 section: {header!r}"

    actual_h2 = tuple(re.findall(r"^## .+$", body, flags=re.MULTILINE))
    assert actual_h2 == expected_h2, (
        f"submit-commit-cleanup-artifact.md must contain exactly seven H2 sections in the "
        f"planned order; got {actual_h2!r}"
    )

    assert "SECURITY BOUNDARY" not in body, (
        "submit-commit-cleanup-artifact.md must NOT contain any dangling "
        "'SECURITY BOUNDARY' cross-reference (the security-boundary rule is "
        "covered under Common Mistakes, not as a standalone section)"
    )

    assert "optional" in body.lower(), "body must explicitly mark the skill as optional"
    assert ".agent/artifact-formats/commit_cleanup.md" in body, (
        "body must reference the commit_cleanup format doc"
    )
    assert "actions" in body, "body must surface the actions array contract"
    assert "retry" in body.lower(), "body must contain retry guidance"
    assert "ralph_submit_artifact" in body, (
        "body must mention the ralph_submit_artifact MCP tool"
    )


def test_submit_commit_message_artifact_skill_documents_breaking_change_marker() -> None:
    """The conventional-commit breaking-change `!` marker must be documented."""
    assert COMMIT_MESSAGE_SKILL_PATH.exists(), (
        f"missing skill: {COMMIT_MESSAGE_SKILL_PATH}"
    )
    _, body = _read_skill(COMMIT_MESSAGE_SKILL_PATH)
    assert "!" in body, (
        "submit-commit-message-artifact.md must document the breaking-change `!` marker"
    )
    needle = (
        "Breaking"
        if "Breaking" in body
        else "breaking"
    )
    assert needle in body, (
        "submit-commit-message-artifact.md must surface the breaking-change "
        "concept alongside the conventional-commit subject shape"
    )
    assert re.search(r"\(\)!\:|!:\s", body), (
        "submit-commit-message-artifact.md must show the breaking-change `!` "
        "placement (e.g. `type!:` or `type(scope)!:`)"
    )


# ---------------------------------------------------------------------------
# AC-08 — Three per-type pointer tests in _raise_format_doc_error
# ---------------------------------------------------------------------------


def test_raise_format_doc_error_mentions_per_type_skill_for_commit_message(
    tmp_path: Path,
) -> None:
    backend = PathFileBackend()
    exc = RuntimeError("synthetic format-doc error")
    with pytest.raises(Exception) as excinfo:
        _raise_format_doc_error("commit_message", tmp_path, backend, exc)
    message = str(excinfo.value)
    assert "submit-artifact" in message, (
        "_raise_format_doc_error must still emit the generic submit-artifact sentence"
    )
    assert "submit-commit-message-artifact" in message, (
        "_raise_format_doc_error must append the per-type skill pointer for commit_message"
    )


def test_raise_format_doc_error_mentions_per_type_skill_for_development_result(
    tmp_path: Path,
) -> None:
    backend = PathFileBackend()
    exc = RuntimeError("synthetic format-doc error")
    with pytest.raises(Exception) as excinfo:
        _raise_format_doc_error("development_result", tmp_path, backend, exc)
    message = str(excinfo.value)
    assert "submit-artifact" in message, (
        "_raise_format_doc_error must still emit the generic submit-artifact sentence"
    )
    assert "submit-development-result-artifact" in message, (
        "_raise_format_doc_error must append the per-type skill pointer for development_result"
    )


def test_raise_format_doc_error_mentions_per_type_skill_for_commit_cleanup(
    tmp_path: Path,
) -> None:
    backend = PathFileBackend()
    exc = RuntimeError("synthetic format-doc error")
    with pytest.raises(Exception) as excinfo:
        _raise_format_doc_error("commit_cleanup", tmp_path, backend, exc)
    message = str(excinfo.value)
    assert "submit-artifact" in message, (
        "_raise_format_doc_error must still emit the generic submit-artifact sentence"
    )
    assert "submit-commit-cleanup-artifact" in message, (
        "_raise_format_doc_error must append the per-type skill pointer for commit_cleanup"
    )


# ---------------------------------------------------------------------------
# AC-09 — Three analysis-template OPTIONAL skill pointer tests
# ---------------------------------------------------------------------------


def _render_template_source(template_path: Path) -> str:
    """Render a .jinja template source with the shared OPTIONAL pointer resolved.

    Renders with empty globals so the include resolves to its literal block.
    Asserts on substring presence, not exact render equality.
    """
    source = template_path.read_text(encoding="utf-8")
    shared_dir = (
        REPO_ROOT / "ralph" / "prompts" / "templates" / "shared"
    )

    partials: dict[str, str] = {}
    for path in shared_dir.rglob("*.jinja"):
        key = path.relative_to(shared_dir.parent).with_suffix("").as_posix()
        partials[key] = path.read_text(encoding="utf-8")
    for path in shared_dir.rglob("*.j2"):
        key = path.relative_to(shared_dir.parent).with_suffix("").as_posix()
        partials[key] = path.read_text(encoding="utf-8")

    env = Environment(
        loader=DictLoader(
            {"__main__.j2": source, **{f"{k}.j2": v for k, v in partials.items()}}
        ),
        autoescape=False,
        keep_trailing_newline=True,
        undefined=ChainableUndefined,
    )
    env.globals["raise_error"] = lambda *_args, **_kwargs: ""
    template = env.get_template("__main__.j2")
    return template.render()


def test_development_analysis_jinja_has_optional_skill_pointer() -> None:
    rendered = _render_template_source(DEVELOPMENT_ANALYSIS_JINJA)
    assert "submit-artifact" in rendered, (
        "development_analysis.jinja must emit the submit-artifact skill pointer"
    )
    assert ".agent/artifact-formats/artifact_formats_index.md" in rendered, (
        "development_analysis.jinja must reference the artifact_formats_index.md doc"
    )


def test_review_analysis_jinja_has_optional_skill_pointer() -> None:
    rendered = _render_template_source(REVIEW_ANALYSIS_JINJA)
    assert "submit-artifact" in rendered, (
        "review_analysis.jinja must emit the submit-artifact skill pointer"
    )
    assert ".agent/artifact-formats/artifact_formats_index.md" in rendered, (
        "review_analysis.jinja must reference the artifact_formats_index.md doc"
    )


def test_planning_analysis_jinja_has_optional_skill_pointer() -> None:
    rendered = _render_template_source(PLANNING_ANALYSIS_JINJA)
    assert "submit-artifact" in rendered, (
        "planning_analysis.jinja must emit the submit-artifact skill pointer"
    )
    assert ".agent/artifact-formats/artifact_formats_index.md" in rendered, (
        "planning_analysis.jinja must reference the artifact_formats_index.md doc"
    )


# ---------------------------------------------------------------------------
# AC-10 — Four developer/commit template per-type skill pointer tests
# ---------------------------------------------------------------------------


def test_commit_message_jinja_has_optional_skill_pointer() -> None:
    rendered = _render_template_source(COMMIT_MESSAGE_JINJA)
    assert "submit-commit-message-artifact" in rendered, (
        "commit_message.jinja must emit the per-type submit-commit-message-artifact skill pointer"
    )
    assert ".agent/artifact-formats/commit_message.md" in rendered, (
        "commit_message.jinja must reference the commit_message.md doc"
    )


def test_developer_iteration_jinja_has_optional_skill_pointer() -> None:
    rendered = _render_template_source(DEVELOPER_ITERATION_JINJA)
    assert "submit-development-result-artifact" in rendered, (
        "developer_iteration.jinja must emit the per-type "
        "submit-development-result-artifact skill pointer"
    )
    assert ".agent/artifact-formats/development_result.md" in rendered, (
        "developer_iteration.jinja must reference the development_result.md doc"
    )


def test_developer_iteration_continuation_jinja_has_optional_skill_pointer() -> None:
    rendered = _render_template_source(DEVELOPER_ITERATION_CONTINUATION_JINJA)
    assert "submit-development-result-artifact" in rendered, (
        "developer_iteration_continuation.jinja must emit the per-type "
        "submit-development-result-artifact skill pointer"
    )
    assert ".agent/artifact-formats/development_result.md" in rendered, (
        "developer_iteration_continuation.jinja must reference the development_result.md doc"
    )


def test_commit_cleanup_jinja_has_optional_skill_pointer() -> None:
    rendered = _render_template_source(COMMIT_CLEANUP_JINJA)
    assert "submit-commit-cleanup-artifact" in rendered, (
        "commit_cleanup.jinja must emit the per-type submit-commit-cleanup-artifact skill pointer"
    )
    assert ".agent/artifact-formats/commit_cleanup.md" in rendered, (
        "commit_cleanup.jinja must reference the commit_cleanup.md doc"
    )


def test_commit_cleanup_jinja_json_fences_are_parseable() -> None:
    text = COMMIT_CLEANUP_JINJA.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, flags=re.DOTALL)
    assert blocks, "commit_cleanup.jinja must contain JSON examples"
    for index, block in enumerate(blocks, start=1):
        try:
            json.loads(block)
        except json.JSONDecodeError as exc:  # pragma: no cover - failure path
            pytest.fail(f"commit_cleanup.jinja JSON block {index} is invalid: {exc}")




# ---------------------------------------------------------------------------
# AC-11 — submit-plan-step-edits.md skill shape (new skill)
# ---------------------------------------------------------------------------


def test_submit_plan_step_edits_skill_shape() -> None:
    assert PLAN_STEP_EDITS_SKILL_PATH.exists(), (
        f"missing skill: {PLAN_STEP_EDITS_SKILL_PATH}"
    )
    fm, body = _read_skill(PLAN_STEP_EDITS_SKILL_PATH)

    assert fm.get("name") == "submit-plan-step-edits"
    assert fm.get("description"), "frontmatter description is required"
    assert fm["description"].startswith("Use when"), (
        f"description must start with 'Use when', got: {fm['description']!r}"
    )
    assert len(fm["description"]) <= 500, (
        f"description must stay under the 500-char soft cap, got {len(fm['description'])}"
    )

    frontmatter_text = PLAN_STEP_EDITS_SKILL_PATH.read_text(encoding="utf-8")
    total_frontmatter = len(re.match(r"---\n.*?\n---", frontmatter_text, re.DOTALL).group(0))
    assert total_frontmatter <= 1024, (
        f"frontmatter must stay under the 1024-char hard cap, got {total_frontmatter}"
    )

    cso_keywords = (
        "cross-section validator failure",
        "step numbering off-by-one",
        "dangling depends_on",
        "orphan AC satisfied_by_steps",
    )
    for keyword in cso_keywords:
        assert keyword in fm["description"], (
            f"description must contain CSO keyword {keyword!r} for trigger-symptom "
            f"discovery; got: {fm['description']!r}"
        )

    expected_h2 = (
        "## Overview",
        "## When to Use",
        "## Core Flow (step mutation)",
        "## Correcting Rejected Step Edits",
        "## Analysis Feedback Corrections",
        "## Source of Truth Reference",
        "## Common Mistakes",
        "## Red Flags - STOP and Start Over",
    )
    for header in expected_h2:
        assert header in body, f"missing H2 section: {header!r}"

    tool_names = (
        "ralph_insert_plan_step",
        "ralph_replace_plan_step",
        "ralph_patch_step",
        "ralph_remove_plan_step",
        "ralph_move_plan_step",
    )
    for tool_name in tool_names:
        assert tool_name in body, f"body must mention {tool_name!r}"

    assert ".agent/artifact-formats/plan.md" in body, (
        "body must reference the plan format doc"
    )
    assert "reindex" in body, "body must surface the reindex semantics"
    assert "orphan" in body, "body must surface the orphan AC drop semantics"
    assert "## Red Flags - STOP and Start Over" in body, (
        "body must end with the Red Flags section per writing-skills.md"
    )
    assert "optional" in body.lower(), "body must explicitly mark the skill as optional"


# ---------------------------------------------------------------------------
# AC-12 — planning.jinja pointer sections removed
# ---------------------------------------------------------------------------


def test_planning_jinja_pointer_sections_removed() -> None:
    source = PLANNING_JINJA.read_text(encoding="utf-8")

    removed_h2 = (
        "## INTENT & INTENT_VERB",
        "## STEP CONTRACT",
        "## PLAN SIZE LIMITS",
        "## CYCLE GUARD",
        "## MODULE FAMILY",
        "## StepType reference",
        "## STEP \u2194 ACCEPTANCE-CRITERIA LINKING",
        "## DESIGN SECTION",
    )
    for header in removed_h2:
        assert f"{header}\n" not in source, (
            f"planning.jinja must NOT contain the deleted H2 heading {header!r}"
        )

    assert "## Plan-artifact canonical contract\n" in source, (
        "planning.jinja must contain the new '## Plan-artifact canonical contract' heading"
    )
    assert "## Common StepType mistakes" in source, (
        "planning.jinja must preserve the '## Common StepType mistakes' section"
    )
    assert "## OPTIONAL: submit-plan-artifact skill" in source, (
        "planning.jinja must preserve the existing '## OPTIONAL: submit-plan-artifact skill' "
        "section (per AC-04 invariants)"
    )


# ---------------------------------------------------------------------------
# AC-13 — planning_fallback.jinja pointer sections removed
# ---------------------------------------------------------------------------


def test_planning_fallback_jinja_pointer_sections_removed() -> None:
    source = PLANNING_FALLBACK_JINJA.read_text(encoding="utf-8")

    removed_h2 = ("## Plan size limits", "## Model tier", "## Cycle guard")
    for header in removed_h2:
        assert f"{header}\n" not in source, (
            f"planning_fallback.jinja must NOT contain the deleted H2 heading {header!r}"
        )

    rendered = _render_template_source(PLANNING_FALLBACK_JINJA)
    heading_count = rendered.count("## OPTIONAL: submit-plan-artifact skill")
    assert heading_count == 1, (
        "planning_fallback.jinja must render exactly one "
        "'## OPTIONAL: submit-plan-artifact skill' heading (was "
        f"{heading_count}); the shared include already emits the heading, so "
        "the source must not duplicate it inline."
    )

    assert "ARTIFACT_HISTORY_PATH" in source and "ARTIFACT_HISTORY_DIR" in source, (
        "planning_fallback.jinja must preserve the ARTIFACT_HISTORY_PATH / "
        "ARTIFACT_HISTORY_DIR tokens"
    )


# ---------------------------------------------------------------------------
# AC-14 — planning_edit*.jinja reference the new submit-plan-step-edits skill
# ---------------------------------------------------------------------------


def test_planning_edit_skill_pointer_wired() -> None:
    edit_source = PLANNING_EDIT_JINJA.read_text(encoding="utf-8")
    edit_fallback_source = PLANNING_EDIT_FALLBACK_JINJA.read_text(encoding="utf-8")

    for source, label in (
        (edit_source, "planning_edit.jinja"),
        (edit_fallback_source, "planning_edit_fallback.jinja"),
    ):
        assert "submit-plan-step-edits" in source, (
            f"{label} must reference the new submit-plan-step-edits skill pointer"
        )
        assert "submit-plan-artifact" in source, (
            f"{label} must preserve the existing submit-plan-artifact skill pointer"
        )


# ---------------------------------------------------------------------------
# AC-15 — _format_plan_step_edit_error mentions submit-plan-step-edits
# ---------------------------------------------------------------------------


def test_format_plan_step_edit_error_mentions_submit_plan_step_edits(
    tmp_path: Path,
) -> None:
    backend = PathFileBackend()
    message = _format_plan_step_edit_error(
        detail="synthetic test detail",
        workspace_root=tmp_path,
        backend=backend,
        tool_name="ralph_insert_plan_step",
    )
    assert "submit-plan-step-edits" in message, (
        "_format_plan_step_edit_error must mention the submit-plan-step-edits skill pointer"
    )
    assert "submit-plan-artifact" in message, (
        "_format_plan_step_edit_error must preserve the existing submit-plan-artifact sentence"
    )
    assert ".agent/artifact-formats/plan.md" in message, (
        "_format_plan_step_edit_error must keep the existing plan.md format-doc reference"
    )
