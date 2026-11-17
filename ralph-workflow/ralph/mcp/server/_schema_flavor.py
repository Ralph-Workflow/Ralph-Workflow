from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject

FUNCTION_CALLING_SCHEMA_CLIENTS: frozenset[str] = frozenset({"kimi-code", "opencode"})

FUNCTION_CALLING_SCHEMA_FLAVOR = "function-calling"

_ROOT_KEYS_PRESERVED: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "description",
        "title",
    }
)


def schema_flavor_for_client_name(client_name: str | None) -> str | None:
    if client_name and client_name in FUNCTION_CALLING_SCHEMA_CLIENTS:
        return FUNCTION_CALLING_SCHEMA_FLAVOR
    return None


def _flatten_property_subschema(subschema: JsonObject) -> JsonObject:
    branches = subschema.get("oneOf") or subschema.get("anyOf")
    if isinstance(branches, list) and "type" not in subschema:
        branch_dicts = [
            cast("dict[str, object]", b)
            for b in cast("list[object]", branches)
            if isinstance(b, dict)
        ]
        branch_type_of = [b.get("type") for b in branch_dicts]
        branch_types = sorted(t for t in branch_type_of if isinstance(t, str))
        if branch_types and len(branch_types) == len(branches):
            flattened_prop: JsonObject = {
                key: subschema[key] for key in _ROOT_KEYS_PRESERVED if key in subschema
            }
            flattened_prop["type"] = branch_types
            array_items = [b.get("items") for b in branch_dicts if b.get("type") == "array"]
            if len(array_items) == 1 and isinstance(array_items[0], dict):
                flattened_prop["items"] = array_items[0]
            return flattened_prop
    if "type" in subschema and any(key in subschema for key in ("oneOf", "anyOf", "allOf", "not")):
        return {key: subschema[key] for key in _ROOT_KEYS_PRESERVED if key in subschema}
    return subschema


def _repair_property_enum_without_type(properties: JsonObject) -> JsonObject:
    repaired: JsonObject = {}
    for name, subschema in properties.items():
        if isinstance(subschema, dict):
            schema_dict = cast("dict[str, object]", subschema)
            enum_members = schema_dict.get("enum")
            if (
                "type" not in schema_dict
                and isinstance(enum_members, list)
                and all(isinstance(member, str) for member in cast("list[object]", enum_members))
            ):
                repaired[name] = {**schema_dict, "type": "string"}
            else:
                repaired[name] = _flatten_property_subschema(schema_dict)
        else:
            repaired[name] = subschema
    return repaired


def normalize_schema_for_function_calling(schema: JsonObject) -> JsonObject:
    flattened: JsonObject = {key: schema[key] for key in _ROOT_KEYS_PRESERVED if key in schema}
    flattened.setdefault("type", "object")
    properties = flattened.get("properties")
    if isinstance(properties, dict):
        flattened["properties"] = _repair_property_enum_without_type(properties)
    return flattened


__all__ = [
    "FUNCTION_CALLING_SCHEMA_CLIENTS",
    "FUNCTION_CALLING_SCHEMA_FLAVOR",
    "normalize_schema_for_function_calling",
    "schema_flavor_for_client_name",
]
