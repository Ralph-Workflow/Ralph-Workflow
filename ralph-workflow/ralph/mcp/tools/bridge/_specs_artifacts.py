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
    EDIT_MD_ARTIFACT_TOOL,
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
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["artifact_type", "content"],
                },
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_submit_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=VERIFY_MD_ARTIFACT_TOOL,
                description="Check a markdown artifact without persisting it; diagnostics match submission.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["artifact_type", "content"],
                },
                required_capability=Capability.ARTIFACT_PLAN_READ.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_verify_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=STAGE_MD_ARTIFACT_TOOL,
                description="Stage a large markdown artifact incrementally: append to (or replace) a persisted draft; returns section outline and non-gating diagnostics.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {"type": "string"},
                        "content": {"type": "string"},
                        "mode": {"enum": ["append", "replace_all"]},
                    },
                    "required": ["artifact_type", "content"],
                },
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_stage_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=EDIT_MD_ARTIFACT_TOOL,
                description=(
                    "Submit an artifact by editing the staged draft in place: this is "
                    f"{SUBMIT_MD_ARTIFACT_TOOL} starting from the existing draft instead of a "
                    "whole retyped document. Applies oldText/newText replacements with the same "
                    "semantics as edit_file (sequential first-occurrence replacement, "
                    "all-or-nothing on a miss), then, whenever the edited draft passes the "
                    "submission gate, submits it canonically through the same path as "
                    f"{SUBMIT_MD_ARTIFACT_TOOL} — no separate finalize call is needed. A draft "
                    "that still has errors is kept for further repair and nothing is submitted; "
                    "the response reports `submitted` either way. Submitting does not end the "
                    "phase, so a later edit in the same attempt simply resubmits. `dry_run` "
                    "previews the diff without writing or submitting. PREFER THIS over resending "
                    "a whole document: use it to fix validation errors and for any revision "
                    "where the new content is substantially similar to the draft."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {"type": "string"},
                        "edits": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "oldText": {"type": "string"},
                                    "newText": {"type": "string"},
                                },
                                "required": ["oldText", "newText"],
                            },
                        },
                        "dry_run": {"type": "boolean"},
                        "expected_content_hash": {"type": "string"},
                    },
                    "required": ["artifact_type", "edits"],
                },
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_edit_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GET_MD_DRAFT_TOOL,
                description="Return the staged markdown draft and its current diagnostics (resume after interruption).",
                input_schema={
                    "type": "object",
                    "properties": {"artifact_type": {"type": "string"}},
                    "required": ["artifact_type"],
                },
                required_capability=Capability.ARTIFACT_PLAN_READ.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_get_md_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DISCARD_MD_DRAFT_TOOL,
                description=(
                    "Discard the staged markdown draft for one artifact type. WARNING: this "
                    "throws away the whole document and forces a full retype. Do NOT use it to "
                    "recover from validation errors or to make a revision whose content is "
                    f"substantially similar to the draft — repair it in place with "
                    f"{EDIT_MD_ARTIFACT_TOOL} instead, which also submits once the draft "
                    "validates. Discard only for a genuine wholesale restart, where almost "
                    "nothing in the current draft survives."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"artifact_type": {"type": "string"}},
                    "required": ["artifact_type"],
                },
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_discard_md_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=FINALIZE_MD_ARTIFACT_TOOL,
                description="Validate the assembled draft with the submission gate and submit it canonically; on failure the draft is kept for repair.",
                input_schema={
                    "type": "object",
                    "properties": {"artifact_type": {"type": "string"}},
                    "required": ["artifact_type"],
                },
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.md_artifact",
            handler_name="handle_finalize_md_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=REPORT_PROGRESS_TOOL,
                description="Report pipeline progress.",
                input_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string"}, "note": {"type": "string"}},
                    "required": ["status"],
                },
                required_capability=McpCapability.RUN_REPORT_PROGRESS.value,
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_report_progress",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DECLARE_COMPLETE_TOOL,
                description="Declare agent completion.",
                input_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_declare_complete",
        ),
        ToolSpec(
            metadata=_metadata(
                name=READ_ENV_TOOL,
                description="Read an environment variable.",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                required_capability=McpCapability.ENV_READ.value,
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_read_env",
        ),
        ToolSpec(
            metadata=_metadata(
                name=COORDINATE_TOOL,
                description="Coordinate parallel work units.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "work_unit_id": {"type": "string"},
                        "payload": {"type": "object"},
                    },
                    "required": ["action"],
                },
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_coordinate",
        ),
    ]
