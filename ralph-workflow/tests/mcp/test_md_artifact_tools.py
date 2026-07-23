"""MCP endpoint behavior for markdown artifact authoring."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.config.mcp_models import McpConfig
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.bridge import tool_specs
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact, handle_verify_md_artifact
from ralph.mcp.tools.names import SUBMIT_MD_ARTIFACT_TOOL, VERIFY_MD_ARTIFACT_TOOL

if TYPE_CHECKING:
    from ralph.mcp.tools.tool_content import ToolContent
from tests.test_tool_artifact_2_helper_memorybackend import MemoryBackend
from tests.test_tool_artifact_2_helper_mocksession import MockSession
from tests.test_tool_artifact_2_helper_mockworkspace import MockWorkspace


def _product_spec() -> str:
    return """---
type: product_spec
---
## Title
- [T1] Markdown artifacts
## Scope
- [S1] Move artifacts to markdown
## Goals
- [G1] Reduce authoring friction
## Users
- [U1] Agents
## Success Criteria
- [C1] Markdown validates
"""


def _payload(result: object) -> dict[str, object]:
    content = cast("ToolContent", result.content[0])
    return cast("dict[str, object]", json.loads(content.text))


def test_markdown_artifact_handlers_verify_and_submit_through_the_same_gate(tmp_path) -> None:
    """PLAN step 11: check-only and submit report identical diagnostics."""
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    params = {"artifact_type": "product_spec", "content": _product_spec()}

    verified = handle_verify_md_artifact(session, workspace, params)
    submitted = handle_submit_md_artifact(
        session,
        workspace,
        params,
        deps=ArtifactHandlerDeps(backend=MemoryBackend()),
    )

    assert verified.is_error is False
    assert submitted.is_error is False
    assert _payload(verified) == {
        "artifact_type": "product_spec",
        "valid": True,
        "diagnostics": [],
    }
    assert _payload(submitted) == _payload(verified)


def test_markdown_artifact_submission_rejects_the_verify_diagnostics(tmp_path) -> None:
    """PLAN step 11: invalid documents are never persisted by submission."""
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    params = {"artifact_type": "product_spec", "content": "---\ntype: product_spec\n---\n"}

    verified = handle_verify_md_artifact(session, workspace, params)
    submitted = handle_submit_md_artifact(session, workspace, params)

    assert verified.is_error is True
    assert submitted.is_error is True
    assert _payload(submitted) == _payload(verified)
    diagnostics = cast("list[dict[str, object]]", _payload(verified)["diagnostics"])
    assert {diagnostic["rule_id"] for diagnostic in diagnostics} >= {"SPEC008"}


def test_markdown_artifact_tools_are_registered() -> None:
    tool_names = {spec.metadata.definition.name for spec in tool_specs(McpConfig())}

    assert {SUBMIT_MD_ARTIFACT_TOOL, VERIFY_MD_ARTIFACT_TOOL} <= tool_names
