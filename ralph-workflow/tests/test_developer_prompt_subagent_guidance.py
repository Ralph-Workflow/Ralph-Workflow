"""S-1/S-2: delivered parallel guidance preserves safe dispatch obligations."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace


@pytest.mark.parametrize(
    "template_name",
    (
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "developer_iteration_fallback.jinja",
    ),
)
def test_s1_s2_delivered_main_guidance_preserves_safe_dispatch(template_name: str) -> None:
    """REPLACE four source pins with public output, including main fallback."""
    prompt = prompt_developer_iteration_xml_with_context(
        TemplateContext.default(),
        DeveloperPromptInputs(prompt_content="Implement API", plan_content="[S-1] API"),
        MemoryWorkspace(root="/workspace"),
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name=template_name,
    )
    delivered = " ".join(prompt.split())

    for obligation in (
        "independent units",
        "concurrently",
        "Never let two agents edit the same file",
        "Paths:",
        "Directories:",
        "When two ready units claim overlapping paths",
        "serialize the conflicting units in dependency order",
        "never widen a worker's file scope",
        "re-cut the units",
        "Worker scope never includes `.agent`, `.git`, or `.worktrees`",
        "A worker cap limits concurrent units, not total units",
        "queue later ready units",
        "no active writer",
        "Wait for all writers",
        "inability to fan out",
        "bounded sequential increments",
        "do not bypass brokered permissions",
        "main session",
    ):
        assert obligation.lower() in delivered.lower(), (template_name, obligation)
