"""Delivered parallel execution guidance states safe ownership and waves."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace


def test_developer_prompts_regression_delivered_parallel_contract() -> None:
    """S-1/S-2: replace overlapping source pins with the delivered main contract."""
    for template in (
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "developer_iteration_fallback.jinja",
    ):
        rendered = prompt_developer_iteration_xml_with_context(
            TemplateContext.default(),
            DeveloperPromptInputs(prompt_content="", plan_content="", plan_path="/plan.md"),
            MemoryWorkspace(),
            SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template,
        )
        normalized = " ".join(rendered.split())
        for obligation in (
            "independent units",
            "concurrently",
            "Never let two agents edit the same file",
            "main session",
            "Paths:",
            "Directories:",
            "Files:",
            ".agent",
            ".git",
            ".worktrees",
            "Do not broaden file ownership",
            "Serialize conflicting ownership",
            "waves",
            "disjoint file ownership",
            "queue",
            "sequentially",
            "permissions",
        ):
            assert obligation in normalized, (template, obligation)
