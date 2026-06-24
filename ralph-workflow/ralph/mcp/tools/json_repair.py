"""Import-safe JSON container repair for MCP tool arguments."""

from __future__ import annotations

import ast
import json
from typing import cast

JSON_CONTAINER_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "acceptance_criteria",
        "actions",
        "analysis_items_addressed",
        "continuation",
        "constraints",
        "criteria",
        "critical_files",
        "depends_on",
        "evidence_refs",
        "expected_evidence",
        "goals",
        "headless_guide_checks",
        "how_to_fix",
        "issues",
        "mcps",
        "observed_breaks",
        "observed_working",
        "open_questions",
        "parallel_plan",
        "plan_items_proven",
        "primary_files",
        "product_behavior",
        "reference_files",
        "risks_mitigations",
        "satisfied_by_steps",
        "satisfies",
        "scope_boundaries",
        "scope_items",
        "skills",
        "steps",
        "success_criteria",
        "targets",
        "users",
        "ux_ui_requirements",
        "verification_strategy",
        "what_came_up_short",
        "work_units",
    }
)
JSON_LIST_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "actions",
        "analysis_items_addressed",
        "criteria",
        "depends_on",
        "evidence_refs",
        "expected_evidence",
        "expected_outputs",
        "forbidden_in_tests",
        "forbidden_patterns",
        "goals",
        "guard_commands",
        "headless_guide_checks",
        "how_to_fix",
        "issues",
        "mcps",
        "non_goals",
        "observed_breaks",
        "observed_working",
        "open_questions",
        "parallel_plan",
        "plan_items_proven",
        "preferred_patterns",
        "primary_files",
        "product_behavior",
        "reference_files",
        "required_test_layers",
        "risks_mitigations",
        "satisfied_by_steps",
        "satisfies",
        "scope_boundaries",
        "scope_items",
        "skills",
        "sources",
        "steps",
        "success_criteria",
        "targets",
        "users",
        "ux_ui_requirements",
        "verification_strategy",
        "what_came_up_short",
        "work_units",
    }
)
_MIN_CODE_FENCE_LINES = 2


def repair_json_containers(value: object) -> object:
    """Decode JSON-looking strings into containers without touching scalars."""
    if isinstance(value, str):
        decoded = _decode_container_text(value)
        if isinstance(decoded, (dict, list)):
            return repair_json_containers(decoded)
        return value
    if isinstance(value, list):
        return [repair_json_containers(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key in JSON_LIST_FIELD_NAMES:
            normalized[key] = _repair_json_list_field(item)
        elif key in JSON_CONTAINER_FIELD_NAMES or isinstance(item, (dict, list)):
            normalized[key] = repair_json_containers(item)
        else:
            normalized[key] = item
    return normalized


def repair_params_for_schema(
    params: dict[str, object],
    schema: dict[str, object],
) -> dict[str, object]:
    """Repair JSON container strings for structured schema fields."""
    properties_obj = schema.get("properties")
    properties: dict[str, object] = (
        cast("dict[str, object]", properties_obj) if isinstance(properties_obj, dict) else {}
    )
    additional = schema.get("additionalProperties")
    repaired: dict[str, object] = {}
    for key, value in params.items():
        field_schema_obj = properties.get(key)
        field_schema: dict[str, object] = (
            cast("dict[str, object]", field_schema_obj)
            if isinstance(field_schema_obj, dict)
            else {}
        )
        if _schema_accepts_array(field_schema, schema):
            repaired[key] = _repair_json_list_field(value)
        elif _schema_accepts_container(field_schema, schema) or (
            key not in properties and additional is True and _looks_like_json_container_text(value)
        ):
            repaired[key] = repair_json_containers(value)
        else:
            repaired[key] = value
    return repaired


def _repair_json_list_field(value: object) -> object:
    item, unwrapped = _unwrap_item_chain(value)
    if unwrapped:
        if isinstance(item, list):
            return item
        return [item]
    return item


def _unwrap_item_chain(value: object) -> tuple[object, bool]:
    repaired = repair_json_containers(value)
    unwrapped = False
    while isinstance(repaired, dict) and len(repaired) == 1 and "item" in repaired:
        unwrapped = True
        repaired = repair_json_containers(repaired["item"])
    return repaired, unwrapped


def _decode_container_text(value: str) -> object:
    for candidate in _container_candidates(value):
        try:
            parsed = cast("object", json.loads(candidate))
        except json.JSONDecodeError:
            parsed = _literal_container(candidate)
        if isinstance(parsed, str):
            nested = _decode_container_text(parsed)
            if isinstance(nested, (dict, list)):
                return nested
        if isinstance(parsed, (dict, list)):
            return parsed
    return value


def _container_candidates(value: str) -> list[str]:
    stripped = value.strip()
    candidates = [stripped] if stripped else []
    fenced = _strip_full_code_fence(stripped)
    if fenced is not None and fenced not in candidates:
        candidates.append(fenced)
    commentless = _strip_json_comments_and_trailing_commas(stripped)
    if commentless and commentless not in candidates:
        candidates.append(commentless)
    return candidates


def _literal_container(candidate: str) -> object | None:
    try:
        parsed = cast("object", ast.literal_eval(candidate))
    except (SyntaxError, ValueError):
        return None
    if isinstance(parsed, (dict, list)) and _is_json_compatible_literal(parsed):
        return parsed
    return None


def _is_json_compatible_literal(value: object) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_compatible_literal(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_compatible_literal(item)
            for key, item in value.items()
        )
    return False


def _strip_full_code_fence(value: str) -> str | None:
    if not value.startswith("```") or not value.endswith("```"):
        return None
    lines = value.splitlines()
    if (
        len(lines) < _MIN_CODE_FENCE_LINES
        or not lines[0].startswith("```")
        or lines[-1].strip() != "```"
    ):
        return None
    return "\n".join(lines[1:-1]).strip()


def _strip_json_comments_and_trailing_commas(value: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        next_char = value[index + 1] if index + 1 < len(value) else ""
        if quote is not None:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(value) and value[index] not in "\r\n":
                index += 1
            continue
        if char == ",":
            next_index = index + 1
            while next_index < len(value) and value[next_index].isspace():
                next_index += 1
            if next_index < len(value) and value[next_index] in ("}", "]"):
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output).strip()


def _schema_accepts_container(
    schema: dict[str, object],
    root_schema: dict[str, object] | None = None,
    seen_refs: frozenset[str] = frozenset(),
) -> bool:
    root = root_schema or schema
    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = None if ref in seen_refs else _resolve_local_ref(root, ref)
        return resolved is not None and _schema_accepts_container(
            resolved,
            root,
            seen_refs | {ref},
        )
    schema_type = schema.get("type")
    if isinstance(schema_type, str) and schema_type in {"object", "array"}:
        return True
    if isinstance(schema_type, list) and any(item in {"object", "array"} for item in schema_type):
        return True
    for combinator in ("anyOf", "oneOf", "allOf"):
        variants_obj = schema.get(combinator)
        if not isinstance(variants_obj, list):
            continue
        for variant in variants_obj:
            if isinstance(variant, dict) and _schema_accepts_container(
                variant,
                root,
                seen_refs,
            ):
                return True
    return False


def _schema_accepts_array(
    schema: dict[str, object],
    root_schema: dict[str, object] | None = None,
    seen_refs: frozenset[str] = frozenset(),
) -> bool:
    root = root_schema or schema
    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = None if ref in seen_refs else _resolve_local_ref(root, ref)
        return resolved is not None and _schema_accepts_array(
            resolved,
            root,
            seen_refs | {ref},
        )
    schema_type = schema.get("type")
    if schema_type == "array":
        return True
    if isinstance(schema_type, list) and "array" in schema_type:
        return True
    for combinator in ("anyOf", "oneOf", "allOf"):
        variants_obj = schema.get(combinator)
        if not isinstance(variants_obj, list):
            continue
        for variant in variants_obj:
            if isinstance(variant, dict) and _schema_accepts_array(
                variant,
                root,
                seen_refs,
            ):
                return True
    return False


def _resolve_local_ref(root_schema: dict[str, object], ref: str) -> dict[str, object] | None:
    if not ref.startswith("#/"):
        return None
    current: object = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    if isinstance(current, dict):
        return current
    return None


def _looks_like_json_container_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped.startswith(("{", "[")) or stripped.startswith("```")


__all__ = [
    "JSON_CONTAINER_FIELD_NAMES",
    "JSON_LIST_FIELD_NAMES",
    "repair_json_containers",
    "repair_params_for_schema",
]
