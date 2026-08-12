"""Rendered prompts describe the runtime's real fallback completion order."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace

_FALLBACK_TEMPLATES = (
    "planning_fallback.jinja",
    "planning_edit_fallback.jinja",
)
_RECEIPT_REQUIREMENT = (
    "A valid artifact receipt—or a validated, promoted fallback receipt—is mandatory."
)
_FINAL_ACTION_SUFFIX = "as the final explicit action."


def _render_fallback(template_name: str, tmp_path: str) -> str:
    context = TemplateContext.default()
    return prompt_planning_xml_with_context(
        context,
        PlanningPromptInputs(prompt_content="Implement the requested change."),
        MemoryWorkspace(root=tmp_path),
        SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        template_name=template_name,
    )


def _render_developer_fallback(tmp_path: str) -> str:
    context = TemplateContext.default()
    return prompt_developer_iteration_xml_with_context(
        context,
        DeveloperPromptInputs(
            prompt_content="Implement the requested change.",
            plan_content="## Steps\n### [S-1] Implement\nType: file_change",
        ),
        MemoryWorkspace(root=tmp_path),
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name="developer_iteration_fallback.jinja",
    )


@pytest.mark.parametrize("template_name", _FALLBACK_TEMPLATES)
def test_direct_submission_requires_receipt_then_durable_sentinel(
    template_name: str,
    tmp_path: Path,
) -> None:
    rendered = _render_fallback(template_name, str(tmp_path))
    normalized = " ".join(rendered.split())

    assert _RECEIPT_REQUIREMENT in normalized
    final_action = "MUST call"
    assert final_action in normalized
    assert _FINAL_ACTION_SUFFIX in normalized
    assert normalized.index(_RECEIPT_REQUIREMENT) < normalized.rindex(final_action)
    completion_clause = normalized[
        normalized.rindex(final_action) : normalized.index(
            _FINAL_ACTION_SUFFIX,
            normalized.rindex(final_action),
        )
    ]
    assert "declare_complete" in completion_clause

    lowered = normalized.lower()
    assert "receipt is sufficient" not in lowered
    assert "receipt alone" not in lowered
    assert "write and stop" not in lowered
    assert "when available" not in lowered


def test_developer_fallback_calls_completion_before_runtime_promotion(
    tmp_path: Path,
) -> None:
    rendered = " ".join(_render_developer_fallback(str(tmp_path)).split())

    write_index = rendered.index("write the same complete document")
    completion_index = rendered.index("immediately after writing the complete fallback")
    promotion_index = rendered.index("completion gate then validates and promotes")

    assert write_index < completion_index < promotion_index
    assert "declare_complete" in rendered[completion_index:promotion_index]


@pytest.mark.parametrize(
    "template_name",
    (
        "developer_iteration_fallback.jinja",
        "worker_developer.jinja",
        "shared/_artifact_submission.j2",
    ),
)
def test_fallback_templates_state_completion_gate_promotes_after_final_call(
    template_name: str,
) -> None:
    source = " ".join(TemplateContext.default().registry.get_template(template_name).split())

    assert "completion gate" in source
    assert "promot" in source
    assert "declare_complete" in source or "DECLARE_COMPLETE_TOOL_REFERENCE" in source
