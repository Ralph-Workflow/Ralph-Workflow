from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.tools.bridge import ToolBridge, ToolDispatchError, build_ralph_tool_registry
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent
    from ralph.mcp.tools.tool_result import ToolResult


_DEFAULT_AGENTS_POLICY = AgentsPolicy(
    agent_chains={
        "planning": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=1000),
        "analysis": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
    },
    agent_drains={
        "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
        "analysis": AgentDrainConfig(chain="analysis", drain_class="analysis"),
    },
)


def _workspace(tmp_path: Path) -> FsWorkspace:
    return FsWorkspace(tmp_path)


def _session_for_drain(drain: str, workspace_path: Path) -> AgentSession:
    plan = build_session_mcp_plan(
        transport=None,
        drain=drain,
        workspace_path=workspace_path,
        agents_policy=_DEFAULT_AGENTS_POLICY,
    )
    return AgentSession(
        session_id=f"{drain}-session",
        run_id=f"{drain}-run",
        drain=drain,
        capabilities=set(plan.capabilities),
    )


def _seed_plan_draft(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    draft = {
        "schema_version": 1,
        "started_at": "2026-05-20T00:00:00+00:00",
        "updated_at": "2026-05-20T00:00:00+00:00",
        "sections": {
            "summary": {
                "context": "Context",
                "scope_items": [
                    {"text": "One", "count": "1", "category": "file_change"},
                    {"text": "Two", "count": "1", "category": "test"},
                    {"text": "Three", "count": "1", "category": "prompt"},
                ],
            },
            "skills_mcp": {
                "skills": [
                    "test-driven-development",
                    "verification-before-completion",
                ],
                "mcps": [],
            },
            "steps": [
                {"number": 1, "title": "First", "content": "first", "depends_on": []},
                {"number": 2, "title": "Second", "content": "second", "depends_on": [1]},
            ],
            "critical_files": {
                "primary_files": [{"path": "a.py", "action": "modify"}],
                "reference_files": [{"path": "b.py", "purpose": "ref"}],
            },
            "risks_mitigations": [
                {"severity": "medium", "risk": "Risk", "mitigation": "Mitigation"}
            ],
            "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
        },
    }
    (artifact_dir / ".plan_draft.json").write_text(json.dumps(draft), encoding="utf-8")


def _draft_steps_after(bridge: ToolBridge, workspace: FsWorkspace) -> list[dict[str, object]]:
    result = cast(
        "ToolResult",
        bridge.dispatch("ralph_get_plan_draft", {}, workspace=workspace),
    )
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    return cast("list[dict[str, object]]", cast("dict[str, object]", payload["draft"])["steps"])


def test_plan_edit_tools_are_visible_only_for_plan_write_sessions(tmp_path: Path) -> None:
    planning_bridge = build_ralph_tool_registry(
        _session_for_drain("planning", tmp_path),
        _workspace(tmp_path),
        upstream_registry=None,
        mcp_config=None,
    )
    analysis_bridge = build_ralph_tool_registry(
        _session_for_drain("analysis", tmp_path),
        _workspace(tmp_path),
        upstream_registry=None,
        mcp_config=None,
    )

    planning_tools = {definition.name for definition in planning_bridge.list_definitions()}
    analysis_tools = {definition.name for definition in analysis_bridge.list_definitions()}

    expected = {"ralph_insert_plan_step", "ralph_replace_plan_step", "ralph_remove_plan_step"}
    assert expected.issubset(planning_tools)
    assert not expected & analysis_tools


def test_insert_plan_step_tool_updates_draft_and_reindexes(tmp_path: Path) -> None:
    _seed_plan_draft(tmp_path)
    workspace = _workspace(tmp_path)
    bridge = build_ralph_tool_registry(
        _session_for_drain("planning", tmp_path),
        workspace,
        upstream_registry=None,
        mcp_config=None,
    )

    bridge.dispatch(
        "ralph_insert_plan_step",
        {
            "index": 2,
            "step": {
                "number": 99,
                "title": "Inserted",
                "content": "inserted",
                "depends_on": [1],
            },
        },
        workspace=workspace,
    )

    steps = _draft_steps_after(bridge, workspace)
    assert [step["number"] for step in steps] == [1, 2, 3]
    assert [step["title"] for step in steps] == ["First", "Inserted", "Second"]


def test_remove_plan_step_tool_requires_plan_write_capability(tmp_path: Path) -> None:
    _seed_plan_draft(tmp_path)
    workspace = _workspace(tmp_path)
    bridge = build_ralph_tool_registry(
        _session_for_drain("analysis", tmp_path),
        workspace,
        upstream_registry=None,
        mcp_config=None,
    )

    with pytest.raises(
        ToolDispatchError,
        match=r"requires capability 'artifact\.plan_write'",
    ):
        bridge.dispatch(
            "ralph_remove_plan_step",
            {"step_number": 1},
            workspace=workspace,
        )
