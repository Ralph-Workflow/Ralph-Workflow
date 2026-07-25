"""MCP endpoint behavior for markdown artifact authoring."""

from __future__ import annotations

import json

import pytest

from ralph.config.mcp_models import McpConfig
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.bridge import tool_specs
from ralph.mcp.tools.md_artifact import (
    REPAIR_HINT,
    handle_submit_md_artifact,
    handle_verify_md_artifact,
)
from ralph.mcp.tools.names import SUBMIT_MD_ARTIFACT_TOOL, VERIFY_MD_ARTIFACT_TOOL
from tests._support.typed_accessors import (
    must_dict_list,
    must_mapping,
)
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
    content = result.content[0]
    return must_mapping(json.loads(content.text))


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
        "counts": {"error": 0, "info": 0, "warning": 0},
        "overridden": [],
        "repair_hint": REPAIR_HINT,
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
    diagnostics = must_dict_list(_payload(verified)["diagnostics"])
    assert {diagnostic["rule_id"] for diagnostic in diagnostics} >= {"SPEC008"}


def test_markdown_artifact_tools_are_registered() -> None:
    tool_names = {spec.metadata.definition.name for spec in tool_specs(McpConfig())}

    assert {SUBMIT_MD_ARTIFACT_TOOL, VERIFY_MD_ARTIFACT_TOOL} <= tool_names


@pytest.mark.parametrize(
    ("label", "content"),
    [
        (
            "duplicate_type_frontmatter",
            "---\ntype: plan\ntype: plan\n---\n## Steps\n\n### [S-1] Step\nType: file_change\nFiles:\n- modify foo.py\n",
        ),
        (
            "malformed_frontmatter",
            "---\ntype: plan\nnot a field\n---\n## Steps\n\n### [S-1] Step\nType: file_change\nFiles:\n- modify foo.py\n",
        ),
        (
            "top_level_prose",
            "---\ntype: plan\n---\nSome prose before any heading.\n## Steps\n\n### [S-1] Step\nType: file_change\nFiles:\n- modify foo.py\n",
        ),
    ],
)
def test_plan_verify_rejects_malformed_markdown(tmp_path, label, content) -> None:
    """The public ``handle_verify_md_artifact`` rejects malformed plan documents.

    Pre-24e66c49f the plan-aware analyze path silently canonicalized
    malformed documents; the rewritten chain keeps the parser
    diagnostics and surfaces them through the tool handler so a real
    agent never sees ``valid=True`` for a plan that the parser cannot
    route.
    """
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    params = {"artifact_type": "plan", "content": content}

    verified = handle_verify_md_artifact(session, workspace, params)

    assert verified.is_error is True, (
        f"malformed plan ({label}) should fail through the public tool path"
    )
    payload = _payload(verified)
    assert payload["valid"] is False
    diagnostics = must_dict_list(payload["diagnostics"])
    assert diagnostics, f"malformed plan ({label}) must surface at least one diagnostic"
    rule_ids = {diagnostic["rule_id"] for diagnostic in diagnostics}
    assert rule_ids & {"MD002", "MD005", "MD006", "MD007"}, (
        f"malformed plan ({label}) should surface a parser-originated error; "
        f"got rule_ids={sorted(rule_ids)!r}"
    )
