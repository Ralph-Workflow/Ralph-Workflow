from __future__ import annotations

from typing import TYPE_CHECKING

from ralph._typed_attrs import attr_or_none
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject

_APPROVED = frozenset({"approved", "allow", "allowed"})


def _mapping_field(mapping: dict[str, object], field: str) -> object | None:
    return mapping.get(field)


def _object_field(outcome: object, field: str) -> object | None:
    if field == "name":
        return attr_or_none(outcome, "name")
    if field == "value":
        return attr_or_none(outcome, "value")
    if field == "status":
        return attr_or_none(outcome, "status")
    return None


def _is_approved(outcome: object) -> bool:
    if outcome is True:
        return True
    if isinstance(outcome, str):
        return outcome.strip().lower() in _APPROVED
    if isinstance(outcome, dict):
        for field in ("name", "value", "status"):
            value = _mapping_field(outcome, field)
            if isinstance(value, str) and value.strip().lower() in _APPROVED:
                return True
        return False
    for field in ("name", "value", "status"):
        value = _object_field(outcome, field)
        if isinstance(value, str) and value.strip().lower() in _APPROVED:
            return True
    return False


def _metadata(
    *,
    name: str,
    description: str,
    input_schema: JsonObject,
    required_capability: str,
) -> ToolMetadata:
    return ToolMetadata(
        definition=ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
        ),
        required_capability=required_capability,
    )
