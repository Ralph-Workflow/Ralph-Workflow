"""Helper functions and constants for building tool specs."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.tools.bridge._types import JsonObject

_EXAMPLE_PLAN_CONTENT = '{"summary": "placeholder"}'
_EXAMPLE_COMMIT_CONTENT = '{"type": "commit", "subject": "placeholder"}'
_EXAMPLE_STEPS_CONTENT = '{"steps": [{"step": "placeholder"}]}'

_SUBMIT_ARTIFACT_DESCRIPTION = (
    "Submit a structured artifact (plan, development_result, issues, etc.). "
    "Required params: artifact_type (string) and content (JSON string). "
    "Returns confirmation on success. "
    'Example: {"artifact_type": "plan", "content": "{\\"summary\\": {}}"} '
    "submits a plan artifact. On error, read the format doc at "
    ".agent/artifact-formats/<type>.md (or .agent/artifact-formats/artifact_formats_index.md "
    "if artifact_type is unknown) before retrying."
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
