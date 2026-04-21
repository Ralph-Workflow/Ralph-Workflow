"""Parse round-trip tests for @EXAMPLE blocks in the bundled default mcp.toml."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ralph.config.mcp_models import McpConfig

_DEFAULTS_TOML = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults" / "mcp.toml"
_MIN_MCP_SERVERS_BLOCKS = 2

_EXAMPLE_BLOCK_RE = re.compile(
    r"# @EXAMPLE-START\n(.*?)\n# @EXAMPLE-END",
    re.DOTALL,
)


def _extract_example_blocks(text: str) -> list[str]:
    return [block.group(1) for block in _EXAMPLE_BLOCK_RE.finditer(text)]


def _uncomment(block: str) -> str:
    lines = []
    for line in block.splitlines():
        if line.startswith("# "):
            lines.append(line[2:])
        elif line == "#":
            lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines)


def test_defaults_toml_is_valid_toml_as_is() -> None:
    text = _DEFAULTS_TOML.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert isinstance(data, dict)


def test_all_example_blocks_produce_valid_toml() -> None:
    text = _DEFAULTS_TOML.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    assert blocks, "no @EXAMPLE blocks found"
    for i, block in enumerate(blocks):
        uncommented = _uncomment(block)
        data = tomllib.loads(uncommented)
        assert isinstance(data, dict), f"block {i} did not produce a dict"


def test_mcp_servers_example_blocks_validate_as_mcp_config() -> None:
    text = _DEFAULTS_TOML.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    validated = 0
    for block in blocks:
        uncommented = _uncomment(block)
        data = tomllib.loads(uncommented)
        if "mcp_servers" not in data:
            continue
        for server_name, spec in data["mcp_servers"].items():
            assert isinstance(spec, dict)
            spec["name"] = server_name
        McpConfig.model_validate(data)
        validated += 1
    assert validated >= _MIN_MCP_SERVERS_BLOCKS, (
        f"expected at least {_MIN_MCP_SERVERS_BLOCKS} mcp_servers @EXAMPLE blocks"
    )


def test_web_search_enabled_block_validates_as_mcp_config() -> None:
    text = _DEFAULTS_TOML.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    for block in blocks:
        uncommented = _uncomment(block)
        data = tomllib.loads(uncommented)
        ws = data.get("web_search", {})
        if not isinstance(ws, dict):
            continue
        if "enabled" in ws or "backend" in ws:
            McpConfig.model_validate(data)
            return
    raise AssertionError("no web_search block with enabled/backend found")


def test_angular_cli_example_block_is_stdio() -> None:
    text = _DEFAULTS_TOML.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    angular_blocks = [b for b in blocks if "angular" in b]
    assert angular_blocks, "no angular-cli @EXAMPLE block found"
    data = tomllib.loads(_uncomment(angular_blocks[0]))
    servers = data.get("mcp_servers", {})
    assert servers, "angular-cli block has no mcp_servers"
    server = next(iter(servers.values()))
    assert server["transport"] == "stdio"


def test_docs_mcp_example_block_is_http_localhost() -> None:
    text = _DEFAULTS_TOML.read_text(encoding="utf-8")
    blocks = _extract_example_blocks(text)
    docs_blocks = [b for b in blocks if "docs-mcp" in b or "localhost:6280" in b]
    assert docs_blocks, "no docs-mcp @EXAMPLE block found"
    data = tomllib.loads(_uncomment(docs_blocks[0]))
    servers = data.get("mcp_servers", {})
    assert servers, "docs-mcp block has no mcp_servers"
    server = next(iter(servers.values()))
    assert server["transport"] == "http"
    assert "6280" in server.get("url", "")
