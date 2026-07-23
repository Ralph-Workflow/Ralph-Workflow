"""Integration coverage for the stable-ID markdown plan edit MCP tool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.md_artifact import handle_edit_md_plan_step
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent

_AGENTS_POLICY = AgentsPolicy(
    agent_chains={
        "planning": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=1000),
        "analysis": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
    },
    agent_drains={
        "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
        "analysis": AgentDrainConfig(chain="analysis", drain_class="analysis"),
    },
)

_PLAN = """---
type: plan
schema_version: 1
---
## Summary
Edit plan steps by stable ID.

Intent: Preserve references while editing.
Coverage: feature, test

## Scope
- [SC-1] Edit a step
  Category: feature
- [SC-2] Preserve stable IDs
  Category: test
- [SC-3] Validate the result
  Category: test

## Skills MCP
Skills: test-driven-development

## Steps

### [S-1] First
Implement the first change.

Type: file_change
Files:
- modify src/first.py

### [S-2] Second
Verify the change.

Type: verify
Depends on: S-1
Verify: pytest -q

## Critical Files
- [CF-1] src/first.py
  Action: modify
  Changes: implement the change

## Risks
- [R-1] References drift
  Severity: high
  Mitigation: Keep IDs stable.

## Verification
- [V-1] pytest -q
  Expect: tests pass
"""


class _Session:
    session_id = "test"
    run_id = ""
    explore_index: object | None = None
    broker_secret: str | None = None

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"


def _edited(params: dict[str, object]) -> str:
    result = handle_edit_md_plan_step(_Session(), cast("object", None), params)
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    return cast("str", payload["content"])


def _session(drain: str, workspace_path: Path) -> AgentSession:
    plan = build_session_mcp_plan(
        transport=None,
        drain=drain,
        workspace_path=workspace_path,
        agents_policy=_AGENTS_POLICY,
    )
    return AgentSession(
        session_id=f"{drain}-session",
        run_id=f"{drain}-run",
        drain=drain,
        capabilities=set(plan.capabilities),
    )


def test_markdown_plan_edit_tool_is_available_to_planning_and_analysis(tmp_path: Path) -> None:
    for drain in ("planning", "analysis"):
        workspace = FsWorkspace(tmp_path)
        bridge = build_ralph_tool_registry(
            _session(drain, tmp_path),
            workspace,
            upstream_registry=None,
            mcp_config=None,
        )
        names = {definition.name for definition in bridge.list_definitions()}
        assert "ralph_edit_md_plan_step" in names


def test_insert_keeps_existing_ids_and_references_stable() -> None:
    replacement = """### [S-9] Inserted
Add an independent file.

Type: file_change
Files:
- create src/inserted.py"""

    edited = _edited(
        {
            "content": _PLAN,
            "action": "insert",
            "step_id": "S-9",
            "replacement": replacement,
            "index": 2,
        }
    )

    assert edited.index("### [S-1]") < edited.index("### [S-9]") < edited.index("### [S-2]")
    assert "Depends on: S-1" in edited


def test_replace_requires_a_matching_stable_id() -> None:
    with pytest.raises(ValueError, match="must match step_id"):
        _edited(
            {
                "content": _PLAN,
                "action": "replace",
                "step_id": "S-1",
                "replacement": "### [S-3] Wrong ID\nText.\n\nType: action",
            }
        )


def test_remove_rejects_a_dangling_dependency() -> None:
    with pytest.raises(Exception, match="unknown step ID 'S-1'"):
        _edited({"content": _PLAN, "action": "remove", "step_id": "S-1"})


def test_move_changes_order_without_renumbering() -> None:
    edited = _edited(
        {"content": _PLAN, "action": "move", "step_id": "S-2", "index": 1}
    )

    assert edited.index("### [S-2]") < edited.index("### [S-1]")
    assert "Depends on: S-1" in edited


def test_replacement_is_markdown_not_a_json_object() -> None:
    with pytest.raises(ValueError, match="single '### \\[S-n\\] Title' step block"):
        _edited(
            {
                "content": _PLAN,
                "action": "replace",
                "step_id": "S-1",
                "replacement": '{"title": "retired JSON shape"}',
            }
        )
