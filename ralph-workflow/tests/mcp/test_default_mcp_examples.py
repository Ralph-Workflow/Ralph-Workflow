from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ralph.config.mcp_models import McpConfig

RALPH_TEST_CHANGE_REASONS = {
    "test_all_example_blocks_produce_valid_toml": (
        "the MCP configuration examples moved out of comment blocks in "
        "ralph/policy/defaults/mcp.toml into the owning document "
        "ralph-workflow/docs/sphinx/mcp-tools.md, because the absolute "
        "comment gate bans comments in tracked TOML; the check still parses "
        "every example block as TOML"
    ),
    "test_mcp_servers_example_blocks_validate_as_mcp_config": (
        "same move of the MCP examples into the owning document; the check "
        "still validates every mcp_servers example as an McpConfig"
    ),
    "test_web_search_enabled_block_validates_as_mcp_config": (
        "same move of the MCP examples into the owning document; the check "
        "still validates the web_search example as an McpConfig"
    ),
    "test_angular_cli_example_block_is_stdio": (
        "same move of the MCP examples into the owning document; the check "
        "still asserts the angular-cli example uses the stdio transport"
    ),
    "test_docs_mcp_example_block_is_http_localhost": (
        "same move of the MCP examples into the owning document; the check "
        "still asserts the docs-mcp example uses the http transport on the "
        "local port"
    ),
}

_DEFAULTS_TOML = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults" / "mcp.toml"
_MCP_TOOLS_DOC = Path(__file__).resolve().parents[2] / "docs" / "sphinx" / "mcp-tools.md"
_MIN_MCP_SERVERS_BLOCKS = 2

_EXAMPLE_BLOCK_RE = re.compile(r"```toml\n(.*?)\n```", re.DOTALL)
_NEXT_SECTION_RE = re.compile(r"^## ", re.MULTILINE)
_EXAMPLES_HEADING = "## Configuration examples"


def _example_section(text: str) -> str:
    start = text.index(_EXAMPLES_HEADING) + len(_EXAMPLES_HEADING)
    rest = text[start:]
    match = _NEXT_SECTION_RE.search(rest)
    return rest[: match.start()] if match is not None else rest

def _extract_example_blocks(text: str) -> list[str]:
    return [block.group(1) for block in _EXAMPLE_BLOCK_RE.finditer(_example_section(text))]


def test_defaults_toml_is_valid_toml_as_is() -> None:
    text = _DEFAULTS_TOML.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert isinstance(data, dict)


def test_all_example_blocks_produce_valid_toml() -> None:
    text = _MCP_TOOLS_DOC.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    assert blocks, "no configuration example blocks found"
    for i, block in enumerate(blocks):
        data = tomllib.loads(block)
        assert isinstance(data, dict), f"block {i} did not produce a dict"


def test_mcp_servers_example_blocks_validate_as_mcp_config() -> None:
    text = _MCP_TOOLS_DOC.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    validated = 0
    for block in blocks:
        data = tomllib.loads(block)
        if "mcp_servers" not in data:
            continue
        for server_name, spec in data["mcp_servers"].items():
            assert isinstance(spec, dict)
            spec["name"] = server_name
        McpConfig.model_validate(data)
        validated += 1
    assert validated >= _MIN_MCP_SERVERS_BLOCKS, (
        f"expected at least {_MIN_MCP_SERVERS_BLOCKS} mcp_servers example blocks"
    )


def test_web_search_enabled_block_validates_as_mcp_config() -> None:
    text = _MCP_TOOLS_DOC.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    for block in blocks:
        data = tomllib.loads(block)
        ws = data.get("web_search", {})
        if not isinstance(ws, dict):
            continue
        if "enabled" in ws or "backend" in ws:
            McpConfig.model_validate(data)
            return
    raise AssertionError("no web_search block with enabled/backend found")


def test_angular_cli_example_block_is_stdio() -> None:
    text = _MCP_TOOLS_DOC.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    angular_blocks = [b for b in blocks if "angular" in b]
    assert angular_blocks, "no angular-cli example block found"
    data = tomllib.loads(angular_blocks[0])
    servers = data.get("mcp_servers", {})
    assert servers, "angular-cli block has no mcp_servers"
    server = next(iter(servers.values()))
    assert server["transport"] == "stdio"


def test_docs_mcp_example_block_is_http_localhost() -> None:
    text = _MCP_TOOLS_DOC.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    docs_blocks = [b for b in blocks if "docs-mcp" in b or "localhost:6280" in b]
    assert docs_blocks, "no docs-mcp example block found"
    data = tomllib.loads(docs_blocks[0])
    servers = data.get("mcp_servers", {})
    assert servers, "docs-mcp block has no mcp_servers"
    server = next(iter(servers.values()))
    assert server["transport"] == "http"
    assert "6280" in server.get("url", "")
