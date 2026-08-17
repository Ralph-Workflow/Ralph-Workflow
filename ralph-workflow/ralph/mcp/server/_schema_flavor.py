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
``"object"``. Direct ``properties`` sub-schemas are additionally
repaired in one narrow way: Moonshot rejects an enum whose parent has
no ``type`` (``At path 'properties.mode': type is not defined``), so a
direct property declaring ``enum`` without ``type`` gains
``"type": "string"`` when every enum member is a string (the observed
``ralph_stage_md_artifact.mode`` case, re-measured against the live
Moonshot API on 2026-08-17). Deeper nesting (property-level ``oneOf``
etc.) is left untouched: the live 42-tool advertisement passes
Moonshot's validator once the composed roots are flattened and the
untyped-enum repair is applied.

The registered :class:`~ralph.mcp.tools.bridge._tool_definition.ToolDefinition`
``input_schema`` is never mutated: dispatch-time validation and every
non-flavored client keep the full JSON Schema contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
        if (
            isinstance(subschema, dict)
            and "enum" in subschema
            and "type" not in subschema
            and isinstance(subschema["enum"], list)
            and all(isinstance(member, str) for member in subschema["enum"])
        ):
            repaired[name] = {**subschema, "type": "string"}
        else:
            repaired[name] = subschema
    return repaired


def flatten_root_schema_for_openai_function(schema: JsonObject) -> JsonObject:
    """Return ``schema`` with an OpenAI-function-flavored root.

    The root keeps the plain-object vocabulary (``type``, ``properties``,
    ``required``, ``additionalProperties``, ``description``, ``title``),
    loses every composition keyword (``oneOf`` / ``anyOf`` / ``allOf`` /
    ``not`` and anything else outside the preserved set), and gains
    ``"type": "object"`` when no root type was declared. Nested property
    sub-schemas are returned by reference, untouched.

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
