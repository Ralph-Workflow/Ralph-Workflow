"""Client-flavored tool-schema advertisement for the MCP server.

Some MCP clients do not consume full JSON Schema. The known case is the
Kimi Code CLI (``kimi-code``), which forwards every ``tools/list`` entry
verbatim as an OpenAI-style ``tools[].function.parameters`` payload when
it calls the Moonshot API. Moonshot validates that payload with a
stricter "moonshot flavored json schema" than JSON Schema draft 2020-12
and rejects a ROOT schema that mixes ``type`` with composition keywords
(``oneOf`` / ``anyOf`` / ``allOf`` / ``not``) or boolean sub-schemas,
failing the whole request with HTTP 400::

    tools.function.parameters is not a valid moonshot flavored json
    schema, details: <At path 'root': when using anyOf, type should be
    defined in anyOf items instead of the parent schema>

Ralph deliberately advertises selector mutual-exclusion contracts
(``read_file``, ``read_multiple_files``, ``exec``) through exactly those
root composition keywords because full JSON Schema clients benefit from
them. Rather than weaken the public contract for every client, the MCP
server negotiates the flavor at the MCP ``initialize`` handshake — the
protocol's own capability-negotiation point — and flattens ONLY the
advertised root schema, ONLY for clients that identify themselves as
needing the OpenAI function flavor.

Flattening keeps the plain-object root vocabulary (``type``,
``properties``, ``required``, ``additionalProperties``, ``description``,
``title``), drops the composition keywords, and defaults ``type`` to
``"object"``. The flattening then recurses one deliberate level into
direct ``properties`` sub-schemas: Moonshot's flavor check is not
root-only (re-measured live on 2026-08-17 — after root-only flattening
shipped, Moonshot still rejected the advertisement with the same
``At path 'root': when using anyOf...`` 400 because
``exec.properties.command/argv/args`` keep ``oneOf`` with no sibling
``type``). For each direct property the same rule is applied: a
composition keyword mixed with a sibling ``type`` is flattened to the
plain vocabulary, and a composition-only property (``exec``'s
``command``/``argv``/``args``, which accept ``string | list[string]``)
is rewritten to ``{"type": ["string", "array"], "items": {...}}`` —
JSON Schema type unions — because Moonshot accepts a bare ``type``
array but never an ``oneOf`` branch list. Direct ``properties``
sub-schemas are additionally repaired in one narrow way: Moonshot
rejects an enum whose parent has no ``type`` (``At path
'properties.mode': type is not defined``), so a direct property
declaring ``enum`` without ``type`` gains ``"type": "string"`` when
every enum member is a string (the observed
``ralph_stage_md_artifact.mode`` case, re-measured against the live
Moonshot API on 2026-08-17). Anything deeper is left untouched: the
live 42-tool advertisement passes Moonshot's validator once the
composed roots, the ``exec`` property unions, and the untyped-enum
repair are applied.

The registered :class:`~ralph.mcp.tools.bridge._tool_definition.ToolDefinition`
``input_schema`` is never mutated: dispatch-time validation and every
non-flavored client keep the full JSON Schema contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject

#: Client ``clientInfo.name`` values (from the MCP ``initialize`` request)
#: whose backing API accepts only the OpenAI function-calling schema
#: flavor. Kimi Code CLI identifies itself as ``kimi-code`` at handshake
#: (verified on the wire: ``"clientInfo": {"name": "kimi-code",
#: "version": "0.0.0"}``).
OPENAI_FUNCTION_FLAVOR_CLIENTS: frozenset[str] = frozenset({"kimi-code"})

#: Sentinel flavor value for clients that need the flattened root schema.
OPENAI_FUNCTION_FLAVOR = "openai-function"

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

    Returns :data:`OPENAI_FUNCTION_FLAVOR` when the client's backing API
    accepts only the OpenAI function-calling schema flavor, ``None``
    otherwise (including unknown, missing, or empty names). ``None`` is
    the default full-JSON-Schema advertisement.
    """
    if client_name and client_name in OPENAI_FUNCTION_FLAVOR_CLIENTS:
        return OPENAI_FUNCTION_FLAVOR
    return None


def _flatten_property_subschema(subschema: JsonObject) -> JsonObject:
    """Flatten one direct ``properties`` sub-schema for Moonshot.

    Same root rule applied one level down: a composition keyword mixed
    with a sibling ``type`` keeps only the plain vocabulary; a
    composition-only sub-schema whose ``oneOf``/``anyOf`` branches all
    declare a plain ``type`` (the ``exec`` ``command``/``argv``/``args``
    ``string | array`` union) is rewritten to a JSON Schema type union
    ``{"type": ["array", "string"]}`` plus the single array branch's
    ``items`` — Moonshot accepts a ``type`` array but never a branch
    list. Any other composition shape (a branch without ``type``, a
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
            # ``string | array`` union): Moonshot accepts a bare ``type``
            # array but never a branch list, so collapse the branches into
            # one JSON Schema type union and lift the array branch's
            # ``items`` when exactly one array branch exists.
            flattened_prop: JsonObject = {
                key: subschema[key]
                for key in _ROOT_KEYS_PRESERVED
                if key in subschema
            }
            flattened_prop["type"] = branch_types
            array_items = [
                b.get("items")
                for b in branch_dicts
                if b.get("type") == "array"
            ]
            if len(array_items) == 1 and isinstance(array_items[0], dict):
                flattened_prop["items"] = array_items[0]
            return flattened_prop
    if "type" in subschema and any(
        key in subschema for key in ("oneOf", "anyOf", "allOf", "not")
    ):
        return {
            key: subschema[key] for key in _ROOT_KEYS_PRESERVED if key in subschema
        }
    return subschema


def _repair_property_enum_without_type(properties: JsonObject) -> JsonObject:
    """Give untyped ``enum`` properties a ``type`` Moonshot accepts.

    Moonshot's validator rejects ``{"enum": [...]}`` with no sibling
    ``type`` (``At path 'properties.mode': type is not defined``). The
    repair adds ``"type": "string"`` when every enum member is a string;
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
                and all(
                    isinstance(member, str)
                    for member in cast("list[object]", enum_members)
                )
            ):
                repaired[name] = {**schema_dict, "type": "string"}
            else:
                repaired[name] = _flatten_property_subschema(schema_dict)
        else:
            repaired[name] = subschema
    return repaired


def flatten_root_schema_for_openai_function(schema: JsonObject) -> JsonObject:
    """Return ``schema`` with an OpenAI-function-flavored root.

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
    flattened: JsonObject = {
        key: schema[key] for key in _ROOT_KEYS_PRESERVED if key in schema
    }
    flattened.setdefault("type", "object")
    properties = flattened.get("properties")
    if isinstance(properties, dict):
        flattened["properties"] = _repair_property_enum_without_type(properties)
    return flattened


__all__ = [
    "OPENAI_FUNCTION_FLAVOR",
    "OPENAI_FUNCTION_FLAVOR_CLIENTS",
    "flatten_root_schema_for_openai_function",
    "schema_flavor_for_client_name",
]
