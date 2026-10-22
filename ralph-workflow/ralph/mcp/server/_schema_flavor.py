"""Portable function-calling schemas for model-routing MCP clients.

OpenCode and Kimi Code can route requests to different model providers. Ralph
therefore cannot select a tool-schema dialect from the configured model name.
Instead, those clients receive a provider-neutral function-calling subset at
the MCP boundary. Full JSON Schema clients retain Ralph's richer validation
contracts.

The normalization keeps plain object keywords, removes root composition, and
normalizes direct property unions. Registered tool definitions are never
mutated, so dispatch-time validation continues to use the complete schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject

FUNCTION_CALLING_SCHEMA_CLIENTS: frozenset[str] = frozenset({"kimi-code", "opencode"})

FUNCTION_CALLING_SCHEMA_FLAVOR = "function-calling"

#: Root keywords preserved by flattening. Everything else at the ROOT
#: (composition keywords, boolean sub-schemas, unknown extras) is dropped.
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
    """Map an MCP ``initialize`` ``clientInfo.name`` to a schema flavor.

    Model-routing clients receive the portable function-calling flavor.
    Unknown clients retain the complete JSON Schema advertisement.
    """
    if client_name and client_name in FUNCTION_CALLING_SCHEMA_CLIENTS:
        return FUNCTION_CALLING_SCHEMA_FLAVOR
    return None


def _flatten_property_subschema(subschema: JsonObject) -> JsonObject:
    """Flatten one direct property for portable function calling.

    Same root rule applied one level down: a composition keyword mixed
    with a sibling ``type`` keeps only the plain vocabulary; a
    composition-only sub-schema whose ``oneOf``/``anyOf`` branches all
    declare a plain ``type`` (the ``exec`` ``command``/``argv``/``args``
    ``string | array`` union) is rewritten to a JSON Schema type union
    ``{"type": ["array", "string"]}`` plus the single array branch's
    ``items``. Any other composition shape (a branch without ``type``, a
    bare ``not``, ...) is returned untouched (never observed from this
    server's registry).
    """
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
            # Composition-only property whose every branch declares a
            # plain ``type`` (e.g. ``exec``'s ``command``/``argv``/``args``
            # ``string | array`` union): collapse the branches into
            # one JSON Schema type union and lift the array branch's
            # ``items`` when exactly one array branch exists.
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
    """Give untyped string-enum properties an explicit portable type.

    The repair adds ``"type": "string"`` when every enum member is a string;
    anything else is returned untouched (never observed from this
    server's registry). Property sub-schemas are copied only when
    repaired; untouched entries keep their identity.
    """
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
    """Return ``schema`` normalized to a portable function-calling root.

    The root keeps the plain-object vocabulary (``type``, ``properties``,
    ``required``, ``additionalProperties``, ``description``, ``title``),
    loses every composition keyword (``oneOf`` / ``anyOf`` / ``allOf`` /
    ``not`` and anything else outside the preserved set), and gains
    ``"type": "object"`` when no root type was declared. Direct
    ``properties`` sub-schemas are flattened one deliberate level down
    via :func:`_flatten_property_subschema` (composition-only string/array
    unions become a ``type`` array); anything deeper is returned by
    reference, untouched.

    The input is never mutated; a new root dict is always returned.
    """
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
