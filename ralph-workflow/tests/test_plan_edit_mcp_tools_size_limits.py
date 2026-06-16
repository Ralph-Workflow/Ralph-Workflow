"""Round-trip tests for the step-wise MCP tools against the new generous caps.

These tests exercise the four step-mutation handlers (insert, replace, remove)
plus the draft finalize against the new `PlanSizeLimits.DEFAULT` caps
(max_string_long=20000 for step content, max_evidence_per_step=500, etc.).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.bridge import ToolBridge
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
    return cast(
        "list[dict[str, object]]",
        cast("dict[str, object]", payload["draft"])["steps"],
    )


def test_insert_step_with_19999_char_content_round_trips(tmp_path: Path) -> None:
    """A step with 19,999 chars of content (just under max_string_long=20000) round-trips."""
    _seed_plan_draft(tmp_path)
    workspace = _workspace(tmp_path)
    bridge = build_ralph_tool_registry(
        _session_for_drain("planning", tmp_path),
        workspace,
        upstream_registry=None,
        mcp_config=None,
    )
    long_content = "x" * 19999
    bridge.dispatch(
        "ralph_insert_plan_step",
        {
            "index": 1,
            "step": {
                "number": 99,
                "title": "Long content",
                "content": long_content,
                "depends_on": [],
            },
        },
        workspace=workspace,
    )
    steps = _draft_steps_after(bridge, workspace)
    assert len(steps) == 3
    assert len(steps[0]["content"]) == 19999


def test_insert_step_with_500_evidence_entries_round_trips(tmp_path: Path) -> None:
    """A step with 500 evidence entries (max_evidence_per_step) round-trips."""
    _seed_plan_draft(tmp_path)
    workspace = _workspace(tmp_path)
    bridge = build_ralph_tool_registry(
        _session_for_drain("planning", tmp_path),
        workspace,
        upstream_registry=None,
        mcp_config=None,
    )
    evidence = [{"kind": "file", "ref": f"file_{i}.py"} for i in range(500)]
    bridge.dispatch(
        "ralph_insert_plan_step",
        {
            "index": 1,
            "step": {
                "number": 99,
                "title": "Many evidence",
                "content": "step with 500 evidence",
                "expected_evidence": evidence,
                "depends_on": [],
            },
        },
        workspace=workspace,
    )
    steps = _draft_steps_after(bridge, workspace)
    assert len(steps[0].get("expected_evidence", [])) == 500


def test_insert_remove_cycle_50_steps_completes(tmp_path: Path) -> None:
    """A 50-step insert/remove cycle completes without errors."""
    _seed_plan_draft(tmp_path)
    workspace = _workspace(tmp_path)
    bridge = build_ralph_tool_registry(
        _session_for_drain("planning", tmp_path),
        workspace,
        upstream_registry=None,
        mcp_config=None,
    )
    for i in range(50):
        current_step_count = len(_draft_steps_after(bridge, workspace))
        bridge.dispatch(
            "ralph_insert_plan_step",
            {
                "index": current_step_count + 1,
                "step": {
                    "number": 100 + i,
                    "title": f"step {i}",
                    "content": f"step {i}",
                    "depends_on": [],
                },
            },
            workspace=workspace,
        )
    steps = _draft_steps_after(bridge, workspace)
    assert len(steps) == 52
    for _ in range(25):
        current = _draft_steps_after(bridge, workspace)
        last_step_number = current[-1]["number"]
        bridge.dispatch(
            "ralph_remove_plan_step",
            {"step_number": last_step_number},
            workspace=workspace,
        )
    steps_after = _draft_steps_after(bridge, workspace)
    assert len(steps_after) == 27


def test_draft_with_200_steps_stays_under_4mb(tmp_path: Path) -> None:
    """A 200-step draft stays under the 4 MB hard cap and serializes cleanly."""
    _seed_plan_draft(tmp_path)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    draft = json.loads((artifact_dir / ".plan_draft.json").read_text(encoding="utf-8"))
    draft["sections"]["steps"] = [
        {"number": i, "title": f"step {i}", "content": f"content {i}", "depends_on": []}
        for i in range(1, 201)
    ]
    draft["sections"]["critical_files"]["primary_files"] = [
        {"path": f"file_{i}.py", "action": "modify"} for i in range(1, 11)
    ]
    (artifact_dir / ".plan_draft.json").write_text(json.dumps(draft), encoding="utf-8")

    workspace = _workspace(tmp_path)
    bridge = build_ralph_tool_registry(
        _session_for_drain("planning", tmp_path),
        workspace,
        upstream_registry=None,
        mcp_config=None,
    )
    result = cast(
        "ToolResult",
        bridge.dispatch("ralph_finalize_plan", {}, workspace=workspace),
    )
    assert result.is_error is False

    final_path = artifact_dir / "plan.json"
    final_bytes = final_path.read_bytes()
    assert len(final_bytes) < 4_000_000, (
        f"Final plan is {len(final_bytes)} bytes, must be under 4_000_000"
    )
