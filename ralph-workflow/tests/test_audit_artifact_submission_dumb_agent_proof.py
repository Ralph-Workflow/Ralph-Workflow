"""Cheap-model safety checks for the shared markdown submission procedure."""

from __future__ import annotations

import re
from importlib import import_module
from pathlib import Path

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_registry import load_partial_templates, packaged_template_root

TEMPLATES_DIR = Path("ralph/prompts/templates")
MACRO_PATH = TEMPLATES_DIR / "shared" / "_artifact_submission.j2"
SINGLE_SHOT_TEMPLATES: tuple[str, ...] = (
    "commit_cleanup.jinja",
    "commit_message.jinja",
    "commit_simplified.jinja",
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "development_analysis.jinja",
    "planning_analysis.jinja",
    "review.jinja",
    "review_analysis.jinja",
    "worker_developer.jinja",
)
_RENDER_CALL_RE = re.compile(
    r"render_artifact_submission\(\s*'(?P<artifact_type>[^']+)'\s*,"
    r"\s*SUBMIT_MD_ARTIFACT_TOOL_REFERENCE\s*,\s*(?P<example_name>[a-z_]+)",
    flags=re.MULTILINE,
)


def _read_macro() -> str:
    return MACRO_PATH.read_text(encoding="utf-8")


def _render_example(artifact_type: str, example_doc: str) -> str:
    partials = load_partial_templates((packaged_template_root(),))
    return render_template(
        (
            "{% from 'shared/_artifact_submission.j2' import render_artifact_submission %}"
            "{{ render_artifact_submission(artifact_type, submit_tool, example_doc, "
            "verify_tool_reference=verify_tool) }}"
        ),
        {
            "artifact_type": artifact_type,
            "submit_tool": "ralph_submit_md_artifact",
            "verify_tool": "ralph_verify_md_artifact",
            "example_doc": example_doc,
            "DECLARE_COMPLETE_TOOL_REFERENCE": "declare_complete",
        },
        partials,
    )


def test_macro_uses_numbered_procedure() -> None:
    content = _read_macro()
    for step in range(1, 6):
        assert f"\n{step}. " in content


def test_macro_names_exact_markdown_call_contract() -> None:
    content = _read_macro()
    assert "artifact_type" in content
    assert "`content`" in content
    assert "full markdown" in content
    assert "never JSON" in content
    assert "ralph_verify_md_artifact" not in content
    assert "verify_tool_reference" in content


def test_rendered_example_is_validator_backed() -> None:
    import_module("ralph.mcp.artifacts.markdown.specs")
    example_doc = """---
type: skip
reason: No committable changes.
---
"""
    rendered = _render_example("commit_message", example_doc)
    match = re.search(r"```markdown\n(?P<document>.*?)\n\s*```", rendered, re.DOTALL)
    assert match is not None
    document = "\n".join(line.removeprefix("   ") for line in match.group("document").splitlines())
    _, diagnostics = parse_and_validate(document, get_spec("commit_message"))
    assert [item for item in diagnostics if item.severity == "error"] == []
    assert "ralph_submit_md_artifact" in rendered
    assert "ralph_verify_md_artifact" in rendered


@pytest.mark.parametrize("template_name", SINGLE_SHOT_TEMPLATES)
def test_each_single_shot_template_uses_markdown_submission_macro(template_name: str) -> None:
    content = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    calls = list(_RENDER_CALL_RE.finditer(content))
    assert calls, f"{template_name} must use render_artifact_submission"
    assert "SUBMIT_ARTIFACT_TOOL_REFERENCE" not in content
    for call in calls:
        assert call.group("artifact_type")
        assert call.group("example_name").endswith("_example")


def test_macro_requires_completion_after_submission() -> None:
    content = _read_macro()
    assert "DECLARE_COMPLETE_TOOL_REFERENCE" in content
    assert "Submit success text is NOT completion" in content
    assert "Step 5 is mandatory" in content


def test_macro_lists_explicit_failure_preventions() -> None:
    content = _read_macro().lower()
    for phrase in (
        "do not guess the grammar",
        "never json",
        "do not",
        "raw markdown text",
        "stop after step 3 or step 4",
    ):
        assert phrase in content


def test_macro_is_concise_enough_for_attention_window() -> None:
    content = _read_macro()
    end = content.find("#}")
    body = content[end + 2 :].strip()
    assert len(body) < 4000
