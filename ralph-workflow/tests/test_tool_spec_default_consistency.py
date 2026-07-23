"""Anti-drift guard: every numeric default advertised in a tool's schema must
equal the default its handler actually applies.

A schema ``default`` (or min/max) that disagrees with the handler constant is the
same defect class as the exec ``timeout_ms`` drift — the agent is told one value
while the tool uses another. The constants live in the (lazily imported) handler
modules; production spec modules deliberately do not import them, so this test
imports both and pins them equal. If either side changes, this fails.
"""

from __future__ import annotations

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.tools import _exec_output_spill, unsafe_exec
from ralph.mcp.tools import exec as exec_tool
from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs
from ralph.mcp.tools.bridge._specs_file_list import file_list_specs
from ralph.mcp.tools.bridge._specs_file_read import file_read_specs
from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.bridge._specs_web_media import web_media_specs
from ralph.mcp.tools.git_read import DEFAULT_LOG_COUNT
from ralph.mcp.tools.names import (
    GIT_LOG_TOOL,
    GREP_FILES_TOOL,
    READ_FILE_TOOL,
    SEARCH_FILES_TOOL,
    STAGE_MD_ARTIFACT_TOOL,
    WEB_SEARCH_TOOL,
)
from ralph.mcp.tools.websearch import _DEFAULT_LIMIT, MAX_LIMIT, MIN_LIMIT
from ralph.mcp.tools.workspace._utils import _GREP_DEFAULT_LIMIT, FULL_READ_DEFAULT_MAX_BYTES


def _prop(specs: list, tool: object, param: str) -> dict:
    for spec in specs:
        if spec.metadata.definition.name == tool:
            props = spec.metadata.definition.input_schema["properties"]
            assert isinstance(props, dict)
            value = props[param]
            assert isinstance(value, dict)
            return value
    raise AssertionError(f"tool {tool!r} not found")


def test_git_log_count_default_matches_handler() -> None:
    assert _prop(git_exec_specs(), GIT_LOG_TOOL, "count")["default"] == DEFAULT_LOG_COUNT


def test_search_and_grep_limit_defaults_match_handler() -> None:
    specs = file_list_specs()
    assert _prop(specs, SEARCH_FILES_TOOL, "limit")["default"] == _GREP_DEFAULT_LIMIT
    assert _prop(specs, GREP_FILES_TOOL, "limit")["default"] == _GREP_DEFAULT_LIMIT


def test_web_search_limit_bounds_match_handler() -> None:
    # websearch's _DEFAULT_LIMIT is module-private; assert via MIN/MAX (public) and
    # the clamp behavior the handler exposes.
    specs = web_media_specs(load_mcp_config(config_path=None))
    limit = _prop(specs, WEB_SEARCH_TOOL, "limit")
    assert limit["minimum"] == MIN_LIMIT
    assert limit["maximum"] == MAX_LIMIT
    assert limit["default"] == _DEFAULT_LIMIT


def test_exec_handlers_share_one_output_spill_path() -> None:
    # Both exec handlers route oversized output through the single shared spill
    # helper, so their inline cap and spill behavior cannot silently diverge.
    assert exec_tool.format_or_spill is _exec_output_spill.format_or_spill
    assert unsafe_exec.format_or_spill is _exec_output_spill.format_or_spill
    assert _exec_output_spill.INLINE_OUTPUT_LIMIT_BYTES > 0


def test_read_file_max_bytes_default_matches_handler() -> None:
    default = _prop(file_read_specs(), READ_FILE_TOOL, "max_bytes")["default"]
    assert default == FULL_READ_DEFAULT_MAX_BYTES


def test_read_file_schema_matches_mutually_exclusive_selector_groups() -> None:
    spec = next(
        spec for spec in file_read_specs() if spec.metadata.definition.name == READ_FILE_TOOL
    )
    schema = spec.metadata.definition.input_schema
    all_of = schema["allOf"]
    assert isinstance(all_of, list)
    forbidden_pairs = {
        tuple(cast_req)
        for entry in all_of
        if isinstance(entry, dict)
        for not_schema in [entry.get("not")]
        if isinstance(not_schema, dict)
        for cast_req in [not_schema.get("required")]
        if isinstance(cast_req, list)
    }
    assert ("line_start", "offset") in forbidden_pairs
    assert ("line_end", "limit") in forbidden_pairs
    assert ("line_start", "head") in forbidden_pairs
    assert ("offset", "tail") in forbidden_pairs


def test_read_file_schema_models_oneof_selector_alternatives() -> None:
    """AC-01: ``read_file`` must expose exactly-one selector
    alternatives (``path`` OR ``evidence_id`` OR ``span_id`` OR
    ``symbol``) through JSON Schema ``oneOf`` so legacy
    ``path`` clients keep working AND selector-only requests
    are accepted before reaching the handler.
    """
    spec = next(
        spec for spec in file_read_specs() if spec.metadata.definition.name == READ_FILE_TOOL
    )
    schema = spec.metadata.definition.input_schema
    one_of = schema.get("oneOf")
    assert isinstance(one_of, list)
    required_keys = {
        tuple(branch.get("required", ()))
        for branch in one_of
        if isinstance(branch, dict)
    }
    assert ("path",) in required_keys
    assert ("evidence_id",) in required_keys
    assert ("span_id",) in required_keys
    assert ("symbol",) in required_keys
    # Each branch must disable the other selector alternatives so
    # mixed selector sets fail ``oneOf`` validation.
    for branch in one_of:
        props = branch.get("properties", {})
        if tuple(branch.get("required", ())) == ("path",):
            assert props.get("evidence_id") is False
            assert props.get("span_id") is False
            assert props.get("symbol") is False


def test_read_multiple_files_schema_models_oneof_paths_or_items() -> None:
    """AC-01: ``read_multiple_files`` must expose exactly-one of
    ``paths`` (legacy) or ``items`` (mixed selector batch) via
    JSON Schema ``oneOf``. Neither supplied or both supplied
    must fail structural validation.
    """
    from ralph.mcp.tools.names import READ_MULTIPLE_FILES_TOOL

    spec = next(
        spec for spec in file_read_specs()
        if spec.metadata.definition.name == READ_MULTIPLE_FILES_TOOL
    )
    schema = spec.metadata.definition.input_schema
    one_of = schema.get("oneOf")
    assert isinstance(one_of, list)
    required_keys = {
        tuple(branch.get("required", ()))
        for branch in one_of
        if isinstance(branch, dict)
    }
    assert ("paths",) in required_keys
    assert ("items",) in required_keys


def test_stage_markdown_artifact_mode_schema_matches_handler_default() -> None:
    mode = _prop(artifact_specs(), STAGE_MD_ARTIFACT_TOOL, "mode")
    assert mode["enum"] == ["append", "replace_all"]
    assert mode.get("default", "append") == "append"
