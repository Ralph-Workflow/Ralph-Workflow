from __future__ import annotations

import importlib
import json
import os
from functools import cache
from pathlib import Path

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import _fallback_http_handler
from ralph.mcp.server._in_memory_transport import drive_request, parse_sse_data
from ralph.mcp.server.runtime import McpServer, build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace
from tests._support.typed_accessors import must_mapping

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN_TOKENS = (
    "build_fastmcp_server",
    "_make_tool_metadata",
    "_create_tool",
    "ToolBuilderLike",
    "ToolManagerLike",
    "func_metadata",
    "anyio.to_thread",
    "FastMCP",
)
FORBIDDEN_TOKEN_BYTES = tuple(token.encode("utf-8") for token in FORBIDDEN_TOKENS)
INCLUDED_SUFFIXES = (".py", ".md")


@cache
def _source_bytes(root: Path) -> tuple[tuple[Path, bytes], ...]:
    sources: list[tuple[Path, bytes]] = []
    for directory, directories, files in os.walk(root):
        directories[:] = [
            name for name in directories if name not in {"__pycache__", ".venv", "node_modules"}
        ]
        base = Path(directory)
        sources.extend(
            (base / name, (base / name).read_bytes())
            for name in files
            if (base / name).suffix in INCLUDED_SUFFIXES
        )
    return tuple(sources)


_RALPH_SOURCE_BYTES = _source_bytes(REPO / "ralph")
_TEST_SOURCE_BYTES = _source_bytes(REPO / "tests")


def test_runtime_module_contains_no_fastmcp_symbols() -> None:
    text = (REPO / "ralph" / "mcp" / "server" / "runtime.py").read_text()
    for token in FORBIDDEN_TOKENS:
        assert token not in text, (
            f"runtime.py must not contain {token!r} after the FastMCP path was deleted"
        )


def test_grep_audit_finds_zero_fastmcp_hits_in_ralph() -> None:
    hits: list[str] = []
    for path, content in _RALPH_SOURCE_BYTES:
        for token, token_bytes in zip(FORBIDDEN_TOKENS, FORBIDDEN_TOKEN_BYTES, strict=True):
            if token_bytes in content:
                rel = path.relative_to(REPO)
                hits.append(f"{rel}: contains {token!r}")
    assert not hits, f"file walk should find no FastMCP references in ralph/, got: {hits}"


def test_in_memory_transport_drives_dispatch_via_saturated_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []
    monkeypatch.setattr(
        _fallback_http_handler._saturated_dispatch,
        "submit",
        lambda c: (seen.append(c), c())[1],
    )
    session = AgentSession(
        session_id="prop-a",
        run_id="prop-a-run",
        drain="standalone",
        capabilities={
            "WorkspaceRead",
            "ArtifactSubmit",
            "RunReportProgress",
        },
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    mcp_server = McpServer(session, workspace, registry)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    status, _headers, body = drive_request(mcp_server, payload)
    assert status == 200
    assert seen, "the dispatch must run through the saturated-dispatch seam"
    data = parse_sse_data(body)
    result = must_mapping(data.get("result", {}))
    assert "tools" in result


def test_ralph_mcp_server_public_surface_has_no_fastmcp_symbol() -> None:
    pkg = importlib.import_module("ralph.mcp.server")
    with pytest.raises(AttributeError):
        _ = pkg.build_fastmcp_server
    runtime = importlib.import_module("ralph.mcp.server.runtime")
    assert not hasattr(runtime, "build_fastmcp_server")
    assert not hasattr(runtime, "FastMCP")
    assert not hasattr(runtime, "_make_tool_metadata")
    assert not hasattr(runtime, "_create_tool")


_ABSENCE_MARKER = "Property A: there is no alternate FastMCP path"

_ABSENCE_DOCUMENTING_TESTS: frozenset[str] = frozenset(
    {
        "test_mcp_server_file_backed_session_capability_profile.py",
        "test_mcp_server_file_backed_session_worker_artifact_dir.py",
        "test_mcp_server_image_content_serialization.py",
        "test_mcp_server_file_backed_session_model_identity.py",
        "test_mcp_server_load_runtime_upstream_servers.py",
    }
)


def _line_is_absence_asserting(line: str, rel: str) -> bool:
    stripped = line.lstrip()
    if _ABSENCE_MARKER in line:
        return True
    if stripped.startswith("#"):
        return True
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    if "FORBIDDEN_TOKENS" in line:
        return True
    return rel.endswith("test_property_a_one_transport_one_behavior.py")


@pytest.mark.timeout_seconds(3)
def test_grep_audit_finds_zero_fastmcp_hits_in_tests() -> None:
    usage_hits: list[str] = []
    absent_files: list[str] = []
    for path, content in _TEST_SOURCE_BYTES:
        if path.suffix != ".py":
            continue
        rel = path.relative_to(REPO).as_posix()
        if not any(token_bytes in content for token_bytes in FORBIDDEN_TOKEN_BYTES):
            continue
        text = content.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        for token in FORBIDDEN_TOKENS:
            for lineno, line in enumerate(lines, start=1):
                if token not in line:
                    continue
                if _line_is_absence_asserting(line, rel):
                    continue
                usage_hits.append(f"{rel}:{lineno}: {token!r} in: {line.strip()}")
        if Path(rel).name in _ABSENCE_DOCUMENTING_TESTS:
            absent_files.append(Path(rel).name)
    assert not usage_hits, (
        "test files contain USAGE hits to FastMCP forbidden tokens; "
        "these must be removed because they re-introduce the alternate "
        f"path. Offending hits: {usage_hits}"
    )
    assert sorted(absent_files) == sorted(_ABSENCE_DOCUMENTING_TESTS), (
        "the set of absence-documenting test files changed; update "
        "_ABSENCE_DOCUMENTING_TESTS in this test to match the new set. "
        f"Found: {sorted(absent_files)}, expected: {sorted(_ABSENCE_DOCUMENTING_TESTS)}"
    )
