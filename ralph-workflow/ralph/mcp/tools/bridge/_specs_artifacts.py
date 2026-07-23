"""Tool specifications for markdown artifact authoring and coordination."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    COORDINATE_TOOL,
    DECLARE_COMPLETE_TOOL,
    DISCARD_MD_DRAFT_TOOL,
    EDIT_MD_PLAN_STEP_TOOL,
    FINALIZE_MD_ARTIFACT_TOOL,
    GET_MD_DRAFT_TOOL,
    READ_ENV_TOOL,
    REPORT_PROGRESS_TOOL,
    STAGE_MD_ARTIFACT_TOOL,
    SUBMIT_MD_ARTIFACT_TOOL,
    VERIFY_MD_ARTIFACT_TOOL,
)


def artifact_specs() -> list[ToolSpec]:
    """Return the markdown-only artifact tool surface."""
    return [
        ToolSpec(
            metadata=_metadata(
                name=SUBMIT_MD_ARTIFACT_TOOL,
                description="Validate and submit one complete markdown artifact document.",
                input_schema={"type": "object", "properties": {"artifact_type": {"type": "string"}, "content": {"type": "string"}}, "required": ["artifact_type", "content"]},
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_submit_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=VERIFY_MD_ARTIFACT_TOOL,
                description="Check a markdown artifact without persisting it; diagnostics match submission.",
                input_schema={"type": "object", "properties": {"artifact_type": {"type": "string"}, "content": {"type": "string"}}, "required": ["artifact_type", "content"]},
                required_capability=Capability.ARTIFACT_PLAN_READ.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_verify_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=STAGE_MD_ARTIFACT_TOOL,
                description="Stage a large markdown artifact incrementally: append to (or replace) a persisted draft; returns section outline and non-gating diagnostics.",
                input_schema={"type": "object", "properties": {"artifact_type": {"type": "string"}, "content": {"type": "string"}, "mode": {"enum": ["append", "replace_all"]}}, "required": ["artifact_type", "content"]},
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_stage_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GET_MD_DRAFT_TOOL,
                description="Return the staged markdown draft and its current diagnostics (resume after interruption).",
                input_schema={"type": "object", "properties": {"artifact_type": {"type": "string"}}, "required": ["artifact_type"]},
                required_capability=Capability.ARTIFACT_PLAN_READ.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_get_md_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DISCARD_MD_DRAFT_TOOL,
                description="Discard the staged markdown draft for one artifact type.",
                input_schema={"type": "object", "properties": {"artifact_type": {"type": "string"}}, "required": ["artifact_type"]},
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_discard_md_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=FINALIZE_MD_ARTIFACT_TOOL,
                description="Validate the assembled draft with the submission gate and submit it canonically; on failure the draft is kept for repair.",
                input_schema={"type": "object", "properties": {"artifact_type": {"type": "string"}}, "required": ["artifact_type"]},
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_finalize_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=EDIT_MD_PLAN_STEP_TOOL,
                description="Edit one markdown plan step by stable S-id and return the updated document; replacement is a markdown step block ('### [S-n] Title' heading plus its body lines), never JSON. Step IDs are stable across edits, so 'Depends on:'/'Satisfied by:' references survive insert, move, and replace.",
                input_schema={"type": "object", "properties": {"content": {"type": "string"}, "action": {"enum": ["insert", "replace", "remove", "move"]}, "step_id": {"type": "string"}, "replacement": {"type": "string"}, "index": {"type": "integer"}}, "required": ["content", "action", "step_id"]},
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_edit_md_plan_step",
        ),
        ToolSpec(
            metadata=_metadata(name=REPORT_PROGRESS_TOOL, description="Report pipeline progress.", input_schema={"type": "object", "properties": {"status": {"type": "string"}, "note": {"type": "string"}}, "required": ["status"]}, required_capability=McpCapability.RUN_REPORT_PROGRESS.value),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_report_progress",
        ),
        ToolSpec(
            metadata=_metadata(name=DECLARE_COMPLETE_TOOL, description="Declare agent completion.", input_schema={"type": "object", "properties": {"summary": {"type": "string"}}}, required_capability=McpCapability.ARTIFACT_SUBMIT.value),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_declare_complete",
        ),
        ToolSpec(
            metadata=_metadata(name=READ_ENV_TOOL, description="Read an environment variable.", input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, required_capability=McpCapability.ENV_READ.value),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_read_env",
        ),
        ToolSpec(
            metadata=_metadata(name=COORDINATE_TOOL, description="Coordinate parallel work units.", input_schema={"type": "object", "properties": {"action": {"type": "string"}, "work_unit_id": {"type": "string"}, "payload": {"type": "object"}}, "required": ["action"]}, required_capability=Capability.ARTIFACT_PLAN_WRITE.value),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_coordinate",
        ),
    ]
