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
from ralph.mcp.tools import exec as exec_tool
from ralph.mcp.tools import unsafe_exec
from ralph.mcp.tools.bridge._specs_file_list import file_list_specs
from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.bridge._specs_web_media import web_media_specs
from ralph.mcp.tools.git_read import DEFAULT_LOG_COUNT
from ralph.mcp.tools.names import (
    GIT_LOG_TOOL,
    GREP_FILES_TOOL,
    SEARCH_FILES_TOOL,
    WEB_SEARCH_TOOL,
)
from ralph.mcp.tools.websearch import _DEFAULT_LIMIT, MAX_LIMIT, MIN_LIMIT
from ralph.mcp.tools.workspace._utils import _GREP_DEFAULT_LIMIT


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


def test_exec_max_output_bytes_constants_agree() -> None:
    # _MAX_OUTPUT_BYTES is defined independently in both exec handlers; not
    # agent-facing, but pin them equal so the two output caps cannot silently
    # diverge.
    assert exec_tool._MAX_OUTPUT_BYTES == unsafe_exec._MAX_OUTPUT_BYTES
