"""Tool specs for artifact, coordination, and planning operations."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.tools.bridge._spec_helpers import (
    _EXAMPLE_PLAN_CONTENT,
    _EXAMPLE_STEPS_CONTENT,
    _SUBMIT_ARTIFACT_DESCRIPTION,
    _metadata,
)
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    COORDINATE_TOOL,
    DECLARE_COMPLETE_TOOL,
    DISCARD_PLAN_DRAFT_TOOL,
    FINALIZE_PLAN_TOOL,
    GET_PLAN_DRAFT_TOOL,
    INSERT_PLAN_STEP_TOOL,
    READ_ENV_TOOL,
    REMOVE_PLAN_STEP_TOOL,
    REPLACE_PLAN_STEP_TOOL,
    REPORT_PROGRESS_TOOL,
    SUBMIT_ARTIFACT_TOOL,
    SUBMIT_PLAN_SECTION_TOOL,
)


def artifact_specs() -> list[ToolSpec]:
    """Return tool specs for artifact, coordination, and planning operations."""
    return [
        ToolSpec(
            metadata=_metadata(
                name=SUBMIT_ARTIFACT_TOOL,
                description=_SUBMIT_ARTIFACT_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {
                            "type": "string",
                            "description": (
                                "Type of artifact as a string. Common types: plan, "
                                "development_result, issues, fix_result, commit_message, "
                                "development_analysis_decision, planning_analysis_decision, "
                                "review_analysis_decision (this list is not exhaustive). "
                                "On an unknown type the error response points to the "
                                "artifact formats index "
                                "(.agent/artifact-formats/artifact_formats_index.md)."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Artifact payload as a JSON-serialized string "
                                "(example values: "
                                + _EXAMPLE_PLAN_CONTENT
                                + ", "
                                + '{"type": "commit", "subject": "placeholder"}'
                                + ")."
                            ),
                        },
                    },
                    "required": ["artifact_type", "content"],
                },
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_submit_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=SUBMIT_PLAN_SECTION_TOOL,
                description=(
                    "Submit one validated plan section. Required: section, content. "
                    "Optional: mode ('replace' or 'append', default 'replace'). "
                    "Sections: summary, skills_mcp, steps, critical_files, "
                    "risks_mitigations, design, verification_strategy, parallel_plan, work_units. "
                    "Call ralph_finalize_plan after staging all sections. "
                    'Example: {"section": "summary", "content": '
                    + _EXAMPLE_PLAN_CONTENT
                    + ', "mode": "replace"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": (
                                "Section name as a string: summary, skills_mcp, steps, "
                                "critical_files, risks_mitigations, verification_strategy, "
                                "parallel_plan, or work_units "
                                "(example values: 'summary', 'steps', "
                                "'risks_mitigations', 'work_units')."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "JSON-serialized section payload as a string "
                                "(example values: "
                                + _EXAMPLE_PLAN_CONTENT
                                + ", "
                                + _EXAMPLE_STEPS_CONTENT
                                + ")."
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "description": (
                                "Mode as a string: 'replace' overwrites the section "
                                "(default), 'append' adds to a list section "
                                "(example values: 'replace', 'append')."
                            ),
                            "default": "replace",
                        },
                    },
                    "required": ["section", "content"],
                },
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_submit_plan_section",
        ),
        ToolSpec(
            metadata=_metadata(
                name=INSERT_PLAN_STEP_TOOL,
                description=(
                    "Insert one plan step at a 1-based index and automatically reindex the whole"
                    " steps list. Required: index (integer), step (object). The provided"
                    " step.number is ignored; numbering is recomputed deterministically."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 1},
                        "step": {"type": "object"},
                    },
                    "required": ["index", "step"],
                },
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.plan_draft_edit",
            handler_name="handle_insert_plan_step",
        ),
        ToolSpec(
            metadata=_metadata(
                name=REPLACE_PLAN_STEP_TOOL,
                description=(
                    "Replace one plan step by its current number and automatically reindex the"
                    " whole steps list. Required: step_number (integer), step (object)."
                    " The provided step.number is ignored."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "step_number": {"type": "integer", "minimum": 1},
                        "step": {"type": "object"},
                    },
                    "required": ["step_number", "step"],
                },
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.plan_draft_edit",
            handler_name="handle_replace_plan_step",
        ),
        ToolSpec(
            metadata=_metadata(
                name=REMOVE_PLAN_STEP_TOOL,
                description=(
                    "Remove one plan step by its current number and automatically reindex the"
                    " whole steps list. Required: step_number (integer)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "step_number": {"type": "integer", "minimum": 1},
                    },
                    "required": ["step_number"],
                },
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.plan_draft_edit",
            handler_name="handle_remove_plan_step",
        ),
        ToolSpec(
            metadata=_metadata(
                name=FINALIZE_PLAN_TOOL,
                description=(
                    "Validate the staged plan draft and write .agent/artifacts/plan.json. "
                    "Fails with an error if required sections are missing; "
                    "the draft is preserved on failure. No parameters required. "
                    "Example: {} validates and writes the plan."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_finalize_plan",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GET_PLAN_DRAFT_TOOL,
                description=(
                    "Return the currently staged plan draft with all sections and contents. "
                    "Useful for resuming after a restart or confirming current state. "
                    "No parameters required. "
                    "Example: {} returns the current draft state."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability=Capability.ARTIFACT_PLAN_READ.value,
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_get_plan_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DISCARD_PLAN_DRAFT_TOOL,
                description=(
                    "Delete the staged plan draft to start fresh. "
                    "No parameters required. Use with caution as this cannot be undone. "
                    "Example: {} deletes the current draft."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_discard_plan_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=REPORT_PROGRESS_TOOL,
                description=(
                    "Report progress status to the agent orchestrator. "
                    "Required param: status (string). Optional param: note (string). "
                    "Returns confirmation on success. "
                    'Example: {"status": "Processing 50/100 files", "note": "Phase 2 of 3"} '
                    "reports current progress with an optional note."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": (
                                "Status message describing current progress as a string "
                                "(example values: 'Processing 50/100 files', "
                                "'Running tests...', 'Complete')."
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                "Optional additional context or details as a string "
                                "(example values: 'Phase 2 of 3', 'Expected: 2 min')."
                            ),
                        },
                    },
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
                description=(
                    "Declare that the agent has completed its task. "
                    "Optional param: summary (string describing what was accomplished). "
                    "Returns confirmation on success. "
                    'Example: {"summary": "Fixed login bug and added tests"} '
                    "signals task completion with a summary."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": (
                                "Summary of what was accomplished as a string "
                                "(example values: 'Fixed login bug', "
                                "'Completed refactor of auth module')."
                            ),
                        },
                    },
                },
                required_capability=McpCapability.ARTIFACT_SUBMIT.value,
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_declare_complete",
        ),
        ToolSpec(
            metadata=_metadata(
                name=READ_ENV_TOOL,
                description=(
                    "Read an environment variable from the Ralph process. "
                    "Required param: name (string). "
                    "Returns the environment variable value as a string, or null if not set. "
                    'Example: {"name": "HOME"} returns the home directory path.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Environment variable name as a string "
                                "(example values: 'HOME', 'PATH', 'USER', 'EDITOR')."
                            ),
                        },
                    },
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
                description=(
                    "Coordinate parallel worker activities. "
                    "Required param: action (string, one of: claim, release, status, ack). "
                    "Optional params: work_unit_id (string) and payload (object). "
                    "Returns coordination result. "
                    'Example: {"action": "claim", "work_unit_id": "task-001"} '
                    "claims the work unit 'task-001'."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": (
                                "Coordination action as a string: claim, release, status, or "
                                "ack (example values: 'claim', 'release', 'status', 'ack')."
                            ),
                        },
                        "work_unit_id": {
                            "type": "string",
                            "description": (
                                "Work unit identifier as a string "
                                "(example values: 'task-001', 'worker-5', 'build-123')."
                            ),
                        },
                        "payload": {
                            "type": "object",
                            "description": (
                                "Optional coordination payload as a key-value object "
                                "(example values: {'priority': 'high'}, {'status': 'ready'})."
                            ),
                        },
                    },
                    "required": ["action"],
                },
                # Coordinate is planning/coordination-only: gated on plan_write (held
                # by planning drains), NOT artifact.submit (held by every drain).
                # handle_coordinate enforces the SAME capability — keep them in sync.
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_coordinate",
        ),
    ]
