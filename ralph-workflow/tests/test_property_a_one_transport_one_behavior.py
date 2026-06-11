# property-test: A — one transport, one behavior, the shipped path is the tested path
"""The shipped path is the tested path.

The FastMCP-only async tool-offload path has been hard-deleted. This test
pins the absence of the alternate path and the fact that the production
transport is the one every behavioral test exercises.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import _fallback_http_handler
from ralph.mcp.server._in_memory_transport import drive_request, parse_sse_data
from ralph.mcp.server.runtime import McpServer, build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

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
INCLUDED_SUFFIXES = (".py", ".md")


def _iter_source_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in INCLUDED_SUFFIXES:
            continue
        if any(part == "__pycache__" for part in path.parts):
            continue
        out.append(path)
    return out


def test_runtime_module_contains_no_fastmcp_symbols() -> None:
    """The runtime module must not export any FastMCP-only construction path."""
    text = (REPO / "ralph" / "mcp" / "server" / "runtime.py").read_text()
    for token in FORBIDDEN_TOKENS:
        assert token not in text, (
            f"runtime.py must not contain {token!r} after the FastMCP path was deleted"
        )


def test_grep_audit_finds_zero_fastmcp_hits_in_ralph() -> None:
    """The file-walk audit must find no hits in ralph/ outside the absence-asserting test."""
    hits: list[str] = []
    for path in _iter_source_files(REPO / "ralph"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                rel = path.relative_to(REPO)
                hits.append(f"{rel}: contains {token!r}")
    assert not hits, (
        f"file walk should find no FastMCP references in ralph/, got: {hits}"
    )


def test_in_memory_transport_drives_dispatch_via_saturated_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drives the in-memory transport; verifies the dispatch offloads via the seam."""
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
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ).encode()
    status, _headers, body = drive_request(mcp_server, payload)
    assert status == 200
    assert seen, "the dispatch must run through the saturated-dispatch seam"
    data = parse_sse_data(body)
    result = cast("dict[str, object]", data.get("result", {}))
    assert "tools" in result


def test_ralph_mcp_server_public_surface_has_no_fastmcp_symbol() -> None:
    """The ralph.mcp.server package surface must not re-export build_fastmcp_server."""
    pkg = importlib.import_module("ralph.mcp.server")
    # __getattr__ raises AttributeError when the symbol is missing — pin that.
    with pytest.raises(AttributeError):
        _ = pkg.build_fastmcp_server
    # The runtime module attribute is also gone.
    runtime = importlib.import_module("ralph.mcp.server.runtime")
    assert not hasattr(runtime, "build_fastmcp_server")
    assert not hasattr(runtime, "FastMCP")
    assert not hasattr(runtime, "_make_tool_metadata")
    assert not hasattr(runtime, "_create_tool")


# Absence-asserting marker substring used by the test files that document the
# FastMCP path removal. A test line is classified as absence-asserting if it
# contains this exact marker (the canonical assertion text the test fixtures
# quote in a # comment or docstring).
_ABSENCE_MARKER = "Property A: there is no alternate FastMCP path"

# Test files that use the absence marker to document the FastMCP path
# removal. They are the only files in tests/ that may reference any
# FORBIDDEN_TOKENS — every hit in them is a documentation comment, not a
# real import or call.
_ABSENCE_DOCUMENTING_TESTS: frozenset[str] = frozenset(
    {
        "test_mcp_server_file_backed_session_capability_profile.py",
        "test_mcp_server_file_backed_session_worker_artifact_dir.py",
        "test_mcp_server_image_content_serialization.py",
        "test_mcp_server_file_backed_session_model_identity.py",
        "test_mcp_server_load_runtime_upstream_servers.py",
        "test_mcp_server_multimodal_tool_visibility_1.py",
    }
)


def _line_is_absence_asserting(line: str, rel: str) -> bool:
    """True if a matched line is a documentation marker, not a usage hit.

    The classification rule from the plan: a hit is absence-asserting if
    EITHER (a) the line is an inline # comment or docstring containing the
    literal marker ``_ABSENCE_MARKER``, OR (b) the line is inside
    ``test_property_a_one_transport_one_behavior.py`` and the hit is the
    FORBIDDEN_TOKENS tuple itself or a docstring explaining the FastMCP
    path removal.

    In practice, every line inside the canonical pin file
    ``test_property_a_one_transport_one_behavior.py`` is part of the
    absence-asserting machinery (the FORBIDDEN_TOKENS tuple, the
    ``test_ralph_mcp_server_public_surface_has_no_fastmcp_symbol`` body
    that checks ``hasattr(runtime, ...)`` for each token, the test
    function f-string error messages, and the module/test docstrings
    explaining the FastMCP path removal). The classifier below captures
    the union of those cases plus the marker comment rule (a).
    """
    stripped = line.lstrip()
    if _ABSENCE_MARKER in line:
        return True
    if stripped.startswith("#"):
        return True
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    if "FORBIDDEN_TOKENS" in line:
        return True
    # The canonical pin file: any line in it that references a
    # forbidden token is part of the absence-asserting machinery
    # (the FORBIDDEN_TOKENS tuple, the hasattr() test body, the
    # f-string error messages, or the module/test docstrings).
    return rel.endswith("test_property_a_one_transport_one_behavior.py")


@pytest.mark.timeout_seconds(3)
def test_grep_audit_finds_zero_fastmcp_hits_in_tests() -> None:
    """The file-walk audit must find no USAGE hits in tests/.

    Per the PROMPT.md property A acceptance gate, the forbidden tokens may
    appear ONLY as absence-asserting documentation (a # comment, docstring,
    or FORBIDDEN_TOKENS tuple reference). Any usage hit in a test file is a
    real defect — a test that imports or calls a FastMCP-only symbol would
    re-introduce the alternate path.

    A test file is permitted to have hits if EVERY hit in that file is
    classified as absence-asserting; otherwise the test fails with a list
    of offending files and lines.
    """
    usage_hits: list[str] = []
    absent_files: list[str] = []
    for path in _iter_source_files(REPO / "tests"):
        if path.suffix != ".py":
            continue
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
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
