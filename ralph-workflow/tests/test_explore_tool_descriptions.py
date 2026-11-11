from __future__ import annotations

from ralph.mcp.explore.deferred_phases import DEFERRED_PHASES
from ralph.mcp.tools.bridge._specs_explore import explore_specs
from ralph.mcp.tools.names import RALPH_INDEX_STATUS_TOOL, RALPH_REINDEX_TOOL

_TOOL_DESCRIPTION_BUDGET = 900
_ARG_DESCRIPTION_BUDGET = 180


def _explore_specs_by_name() -> dict[str, object]:
    return {spec.metadata.definition.name: spec for spec in explore_specs()}


def test_explore_specs_contains_status_and_reindex() -> None:
    names = {spec.metadata.definition.name for spec in explore_specs()}
    assert RALPH_INDEX_STATUS_TOOL in names
    assert RALPH_REINDEX_TOOL in names


def test_changed_explore_tool_schemas_are_compact_and_complete() -> None:
    specs = _explore_specs_by_name()
    for name, spec in specs.items():
        description = str(spec.metadata.definition.description)
        assert len(description) <= _TOOL_DESCRIPTION_BUDGET, (
            f"{name}: description {len(description)} chars exceeds budget "
            f"{_TOOL_DESCRIPTION_BUDGET}"
        )
        properties = spec.metadata.definition.input_schema.get("properties", {})
        for arg_name, arg_schema in properties.items():
            arg_desc = str(arg_schema.get("description", ""))
            assert len(arg_desc) <= _ARG_DESCRIPTION_BUDGET, (
                f"{name}.{arg_name}: description {len(arg_desc)} chars "
                f"exceeds budget {_ARG_DESCRIPTION_BUDGET}"
            )


def test_ralph_index_status_description_includes_required_checklist_fields() -> None:
    spec = _explore_specs_by_name()[RALPH_INDEX_STATUS_TOOL]
    description = str(spec.metadata.definition.description)
    lowered = description.lower()
    assert "report" in lowered
    assert "returns" in lowered


def test_ralph_index_status_description_documents_fallback_contract() -> None:
    spec = _explore_specs_by_name()[RALPH_INDEX_STATUS_TOOL]
    description = str(spec.metadata.definition.description)
    lowered = description.lower()
    assert "fall through" in lowered or "fallback" in lowered, (
        "status description must explain the auto-mode fall-through contract"
    )
    assert "index_stale_scope" in description, (
        "status description must surface a canonical reason code so the "
        "fallback vocabulary is discoverable from the tool surface"
    )
    for state in ("healthy", "building", "stale", "degraded", "unhealthy"):
        assert state in lowered, f"status description must list the {state!r} health state"
    assert "5%" in description, "status description must name the 5% staleness threshold"
    assert "automatic" in lowered, "status description must state recovery is automatic"
    assert "watch" in lowered, "status description must surface the OS watch-handle guarantee"
    assert "performance" in lowered and "target" in lowered, (
        "status description must point at the performance targets"
    )
    assert len(description) <= _TOOL_DESCRIPTION_BUDGET


def test_ralph_reindex_description_includes_required_checklist_fields() -> None:
    spec = _explore_specs_by_name()[RALPH_REINDEX_TOOL]
    description = str(spec.metadata.definition.description)
    lowered = description.lower()
    assert "reindex" in lowered
    assert "timeout" in lowered or "budget" in lowered
    assert "fail" in lowered
    assert "returns" in lowered


def test_ralph_reindex_mode_enum_is_prompt_exact() -> None:
    spec = _explore_specs_by_name()[RALPH_REINDEX_TOOL]
    mode_schema = spec.metadata.definition.input_schema["properties"]["mode"]
    enum = set(mode_schema["enum"])
    assert enum == {"changed", "full"}


def test_ralph_reindex_timeout_ms_is_positive_integer() -> None:
    spec = _explore_specs_by_name()[RALPH_REINDEX_TOOL]
    timeout = spec.metadata.definition.input_schema["properties"]["timeout_ms"]
    assert timeout["type"] == "integer"
    assert int(timeout["default"]) > 0


def test_ralph_reindex_cancel_is_bounded_bool() -> None:
    spec = _explore_specs_by_name()[RALPH_REINDEX_TOOL]
    properties = spec.metadata.definition.input_schema["properties"]
    assert "cancel" in properties, (
        "ralph_reindex must expose a cancel argument so the bounded "
        "cancellation contract is part of the tool's public surface"
    )
    cancel_schema = properties["cancel"]
    assert cancel_schema["type"] == "boolean"
    assert cancel_schema.get("default", False) is False
    assert "cancel" in str(cancel_schema.get("description", "")).lower()


def test_explore_catalog_token_count_stays_bounded() -> None:
    text = " ".join(str(spec.metadata.definition.description) for spec in explore_specs())
    token_count = len(text.split())
    assert token_count < 1500, f"explore catalog token count {token_count} exceeds soft cap 1500"


def test_deferred_explore_tools_have_no_schemas_yet() -> None:
    names = {spec.metadata.definition.name for spec in explore_specs()}
    assert "ralph_graph" in names
    deferred_ids = {entry.phase_id for entry in DEFERRED_PHASES}
    assert deferred_ids == {"phase_5"}
