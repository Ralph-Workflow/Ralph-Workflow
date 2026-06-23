"""Tool specs for artifact, coordination, and planning operations."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.tools.bridge._spec_helpers import (
    _EXAMPLE_PLAN_SECTION_CONTENT,
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
    MOVE_PLAN_STEP_TOOL,
    PATCH_PLAN_STEP_TOOL,
    READ_ENV_TOOL,
    REMOVE_PLAN_STEP_TOOL,
    REPLACE_PLAN_STEP_TOOL,
    REPORT_PROGRESS_TOOL,
    SUBMIT_ARTIFACT_TOOL,
    SUBMIT_PLAN_SECTION_TOOL,
    SUBMIT_PLAN_SECTIONS_TOOL,
    VALIDATE_PLAN_DRAFT_TOOL,
)


def artifact_specs() -> list[ToolSpec]:
    """Return tool specs for artifact, coordination, and planning operations."""
    return [
        ToolSpec(
            metadata=_metadata(
                name=SUBMIT_ARTIFACT_TOOL,
                description=(
                    _SUBMIT_ARTIFACT_DESCRIPTION
                    + " Do not use this generic tool for plan artifacts; plans must go"
                    " through ralph_submit_plan_section, ralph_submit_plan_sections,"
                    " and ralph_finalize_plan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {
                            "type": "string",
                            "description": (
                                "Type of artifact as a string. Common types: "
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
                                + '{"type": "commit", "subject": '
                                + '"fix(auth): prevent token expiry race"}'
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
                    "Optional: mode (replace|append, default replace). "
                    "Sections: summary, skills_mcp, steps, critical_files, "
                    "risks_mitigations, constraints, design, verification_strategy, "
                    "parallel_plan, work_units. "
                    "Call ralph_finalize_plan after staging all sections. "
                    'Example: {"section": "summary", "content": '
                    + _EXAMPLE_PLAN_SECTION_CONTENT
                    + ', "mode": "replace"}. '
                    "Only the single submitted section is validated here; cross-section "
                    "invariants (depends_on cycle, parallel_plan XOR work_units, "
                    "shell-invocation guard, research/verify in AC.satisfied_by_steps, "
                    "and non-empty skills_mcp.skills) "
                    "run ONLY at finalize_plan. Pass content as the native JSON "
                    "object/array for that section. "
                    "mode=append is list-sections only; "
                    "object sections only accept mode=replace. Plans over the 4 MB cap "
                    "or PlanSizeLimits per-list caps are rejected with PlanArtifactSizeError."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": (
                                "Section name as a string: summary, skills_mcp, steps, "
                                "critical_files, risks_mitigations, constraints, "
                                "design, verification_strategy, parallel_plan, or work_units "
                                "(example values: 'summary', 'steps', "
                                "'risks_mitigations', 'design', 'work_units')."
                            ),
                        },
                        "content": {
                            "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}],
                            "description": (
                                "Section payload as the native JSON object/array for the "
                                "selected section "
                                "(example values: "
                                + _EXAMPLE_PLAN_SECTION_CONTENT
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
                name=SUBMIT_PLAN_SECTIONS_TOOL,
                description=(
                    "Batched section submit. Accepts a list of "
                    "{section: str, content: <json payload>, mode: 'replace'|'append'} "
                    "entries and validates ALL of them BEFORE any merge; if any entry "
                    "fails, the entire batch is rejected and the on-disk draft is "
                    "unchanged. On success it stages every entry and returns "
                    "{submitted: [...section names...], staged_sections: [...], "
                    "total_bytes: <int>}. Use this only when every entry already "
                    "contains complete, analysis-ready section content. content must be "
                    "the native JSON object/array for that section. For list "
                    "sections, mode='append' accepts either one item object or an array "
                    "of items. The full cross-section validator still "
                    "runs at finalize_plan; this tool only stages sections. Capability: "
                    "ARTIFACT_PLAN_WRITE."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "entries": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "section": {"type": "string"},
                                    "content": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {"type": "object"},
                                            {"type": "array"},
                                        ]
                                    },
                                    "mode": {
                                        "type": "string",
                                        "enum": ["replace", "append"],
                                        "default": "replace",
                                    },
                                },
                                "required": ["section", "content"],
                            },
                            "description": (
                                "List of {section, content, mode} entries to stage as a "
                                "batch. Each entry is validated BEFORE any merge; the "
                                "entire batch is rejected on the first failure."
                            ),
                        },
                    },
                    "required": ["entries"],
                },
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_submit_plan_sections",
        ),
        ToolSpec(
            metadata=_metadata(
                name=INSERT_PLAN_STEP_TOOL,
                description=(
                    "Insert one plan step at a 1-based index and reindex the whole steps list. "
                    "Required: index (integer), step (object). The step number in the step "
                    "object is ignored. Auto-reindexes remaining steps, rewrites every "
                    "depends_on array, and rewrites every AC.satisfied_by_steps reference in "
                    "the design sub-section. Returns an echo payload with the new step number, "
                    "the reindex map, the list of rewritten depends_on step numbers, the list "
                    "of rewritten AC ids, the list of dropped AC ids (orphan references), and "
                    "the new total step count: {action: 'insert', index, new_step_number, "
                    "reindex_map, rewritten_depends_on, rewritten_ac_satisfied_by_steps, "
                    "dropped_ac_satisfied_by_steps, total_steps}. Example: "
                    "{'index': 3, 'step': {'title': 'Document the foo() clamp behavior', "
                    "'content': 'Update docs/foo.md with the accepted out-of-range index "
                    "behavior after the code and focused regression test are in place.', "
                    "'step_type': 'file_change', 'targets': [{'path': 'docs/foo.md', "
                    "'action': 'modify'}], 'depends_on': [2], 'expected_evidence': "
                    "[{'kind': 'file', 'ref': 'docs/foo.md'}, {'kind': 'command_output', "
                    "'ref': 'pytest tests/test_foo.py -q'}]}}."
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
                    "Replace one plan step by its current number and reindex the whole steps list. "
                    "Required: step_number (integer), step (object). The step number in the step "
                    "object is ignored. Auto-reindexes remaining steps, rewrites every "
                    "depends_on array, and rewrites every AC.satisfied_by_steps reference in "
                    "the design sub-section. The reindex map is typically a no-op since the "
                    "step number is preserved. Returns an echo payload with the (unchanged) "
                    "step number, the reindex map, the list of rewritten depends_on step "
                    "numbers, the list of rewritten AC ids, the list of dropped AC ids, and "
                    "the new total step count: {action: 'replace', step_number, reindex_map, "
                    "rewritten_depends_on, rewritten_ac_satisfied_by_steps, "
                    "dropped_ac_satisfied_by_steps, total_steps}. Use this when planning "
                    "analysis feedback says a step is vague or missing software-engineering proof: "
                    "replace the full step with concrete content, targets, satisfies, "
                    "expected_evidence, and depends_on arrays. Example step object: "
                    "{title: 'Clamp the foo() index', content: 'Update src/foo.py while "
                    "preserving the public foo() signature.', step_type: 'file_change', "
                    "targets: [{path: 'src/foo.py', action: 'modify'}], satisfies: ['AC-02'], "
                    "expected_evidence: [{kind: 'file', ref: 'src/foo.py'}, {kind: "
                    "'test_name', ref: "
                    "'tests/test_foo.py::test_clamp_handles_out_of_range_index'}], "
                    "depends_on: [1]}."
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
                name=PATCH_PLAN_STEP_TOOL,
                description=(
                    "Partial-update a single plan step. Required: step_number (integer),"
                    " step (object with ANY SUBSET of step fields). The missing fields are"
                    " preserved from the existing step. The provided `step.number` is"
                    " ignored (replace_plan_step forces the number to step_number). The"
                    " step-mutation auto-reindex of `depends_on` and `AC.satisfied_by_steps`"
                    " runs as for `ralph_replace_plan_step`. Returns the same echo payload"
                    " as `ralph_replace_plan_step`: "
                    "{action: 'replace', step_number, reindex_map,"
                    " rewritten_depends_on, rewritten_ac_satisfied_by_steps,"
                    " dropped_ac_satisfied_by_steps, total_steps}. Use this instead of"
                    " `ralph_replace_plan_step` when only one or two fields need to"
                    " change. For analysis feedback like 'step lacks targets/evidence',"
                    " patch just those proof fields: {targets: [{path: 'src/foo.py',"
                    " action: 'modify'}], expected_evidence: [{kind: 'file',"
                    " ref: 'src/foo.py'}], depends_on: [1]}. Capability:"
                    " ARTIFACT_PLAN_WRITE."
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
            handler_name="handle_patch_step",
        ),
        ToolSpec(
            metadata=_metadata(
                name=REMOVE_PLAN_STEP_TOOL,
                description=(
                    "Remove one plan step by its current number and reindex the whole steps list. "
                    "Required: step_number (integer). Auto-reindexes remaining steps, rewrites "
                    "every depends_on array, and rewrites every AC.satisfied_by_steps reference "
                    "in the design sub-section. Fails fast with PlanArtifactValidationError if "
                    "any other step depends on the removed step. Silently drops AC entries "
                    "whose satisfied_by_steps reference the removed step. Returns an echo "
                    "payload with the removed step number, the reindex map, the list of "
                    "rewritten depends_on step numbers, the list of rewritten AC ids, the "
                    "list of dropped AC ids, and the new total step count: "
                    "{action: 'remove', removed_step_number, reindex_map, "
                    "rewritten_depends_on, rewritten_ac_satisfied_by_steps, "
                    "dropped_ac_satisfied_by_steps, total_steps}."
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
                name=MOVE_PLAN_STEP_TOOL,
                description=(
                    "Move one plan step to a 1-based index in a single call. Required: "
                    "from_step_number (integer), to_index (integer; clamped to [1, len+1]). "
                    "Equivalent to remove_plan_step + insert_plan_step but exposed as one "
                    "round-trip. Auto-reindexes remaining steps, rewrites every depends_on "
                    "array, and rewrites every AC.satisfied_by_steps reference. Returns an "
                    "echo payload with the source and target step numbers (typically "
                    "identical since move preserves step numbers), the reindex map "
                    "(typically a no-op), the list of rewritten depends_on step numbers, "
                    "the list of rewritten AC ids, the list of dropped AC ids, and the new "
                    "total step count: {action: 'move', from_step_number, to_index, "
                    "reindex_map, rewritten_depends_on, rewritten_ac_satisfied_by_steps, "
                    "dropped_ac_satisfied_by_steps, total_steps}."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "from_step_number": {"type": "integer", "minimum": 1},
                        "to_index": {"type": "integer", "minimum": 1},
                    },
                    "required": ["from_step_number", "to_index"],
                },
                required_capability=Capability.ARTIFACT_PLAN_WRITE.value,
            ),
            module_name="ralph.mcp.tools.plan_draft_edit",
            handler_name="handle_move_plan_step",
        ),
        ToolSpec(
            metadata=_metadata(
                name=VALIDATE_PLAN_DRAFT_TOOL,
                description=(
                    "Read-only. Runs the full PlanArtifact cross-section validator"
                    " (depends_on cycle, intent_verb vs scope_item category, parallel_plan"
                    " XOR work_units, shell-invocation guard, research/verify steps in AC"
                    " satisfied_by_steps, AC id pattern, non-empty skills_mcp.skills,"
                    " 4 MB size cap) without writing"
                    " plan.json and without deleting the in-progress draft. Returns"
                    " {valid: true} on success or {valid: false, errors: [...]} on"
                    " failure. If no draft exists, returns valid=false with a named"
                    " missing-draft error. The same checks run at finalize_plan in the write path;"
                    " ralph_validate_draft exposes them in a read-only path so the agent"
                    " can dry-run validation before committing. Use errors from this tool"
                    " as analysis feedback: add concrete targets, task-relevant skills,"
                    " acceptance-criterion links, expected evidence, and exact verification"
                    " commands before finalizing. Capability:"
                    " ARTIFACT_PLAN_READ."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability=Capability.ARTIFACT_PLAN_READ.value,
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_validate_plan_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=FINALIZE_PLAN_TOOL,
                description=(
                    "Validate the staged plan draft and write .agent/artifacts/plan.json. "
                    "Fails with an error if required sections are missing or any"
                    " cross-section invariant is violated; the draft is preserved on"
                    " failure. No parameters required. Example: {} validates and writes"
                    " the plan. On success, the in-progress plan draft is DELETED (the"
                    " canonical plan.json is written to .agent/artifacts/plan.json and the"
                    " markdown handoff to .agent/PLAN.md). To recover the draft, use"
                    " ralph_get_plan_draft BEFORE finalize; after finalize the draft is"
                    " gone. If the plan has a depends_on cycle, finalize is rejected with"
                    " the named entry step in the error message:"
                    " `plan step depends_on cycle detected at step N`."
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
                    "No parameters required. Example: {} returns the current draft state."
                    " If the in-progress draft is gone (e.g. after a successful finalize"
                    " or a discarded draft) but a finalized plan.json exists on disk,"
                    " returns the finalized plan with source='finalized_plan'. The"
                    " response shape is {staged_sections: [...], draft: {...},"
                    " source: 'draft'|'finalized_plan', updated_at: ...}."
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
