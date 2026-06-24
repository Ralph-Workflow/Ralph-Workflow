"""Helper functions and constants for building tool specs."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.tools.bridge._types import JsonObject

_EXAMPLE_PLAN_SECTION_CONTENT = (
    '{"context": "Tweak the config key", "scope_items": '
    '[{"text": "Edit config/app.yml"}, {"text": "Verify reload"}, '
    '{"text": "Document the change"}]}'
)
_EXAMPLE_STEPS_CONTENT = (
    '[{"number": 1, "title": "Edit config", "content": "Update the '
    'config key.", "step_type": "file_change", "targets": '
    '[{"path": "config/app.yml", "action": "modify"}]}]'
)

_SUBMIT_ARTIFACT_DESCRIPTION = (
    "Submit a structured artifact. Required: artifact_type (string) and "
    "content (native JSON object/array or JSON-serialized string). Returns confirmation. "
    'Example: {"artifact_type": "commit_message", "content": {"type":'
    '"commit","subject":"type(scope): description"}}. '
    "See .agent/artifact-formats/<type>.md on error. Validated against the "
    "artifact's Pydantic model; artifact payloads enforce the 4 MB cap where "
    "the format defines one."
)


def _is_approved(outcome: object) -> bool:
    if outcome is True:
        return True
    if isinstance(outcome, str):
        return outcome.strip().lower() in {"approved", "allow", "allowed"}
    if isinstance(outcome, dict):
        mapping = cast("Mapping[str, object]", outcome)
        return any(
            isinstance(mapping.get(field), str)
            and cast("str", mapping[field]).strip().lower() in {"approved", "allow", "allowed"}
            for field in ("name", "value", "status")
        )
    for field in ("name", "value", "status"):
        value = cast("object", getattr(outcome, field, None))
        if isinstance(value, str) and value.strip().lower() in {"approved", "allow", "allowed"}:
            return True
    return False


def _metadata(
    *,
    name: str,
    description: str,
    input_schema: JsonObject,
    required_capability: str,
    is_multimodal: bool = False,
) -> ToolMetadata:
    return ToolMetadata(
        definition=ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
        ),
        required_capability=required_capability,
        is_multimodal=is_multimodal,
    )
