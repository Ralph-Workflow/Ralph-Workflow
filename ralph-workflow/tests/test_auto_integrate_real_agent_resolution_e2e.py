"""Minimal live-session proof for auto-integration conflict resolution.

The real-Git rebase/fast-forward composition is covered by the injected-resolver
E2E. This file retains the unique reality check: a deterministic executable is
launched through the production effect executor, edits a conflicted file through
Ralph's live MCP endpoint, and declares completion.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.conflict_resolution import RebaseStop
from ralph.pipeline.factory import PipelineDeps
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

def _write_mcp_agent(root: Path) -> Path:
    script = root / "mcp-resolution-agent.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
import urllib.request
from pathlib import Path

endpoint = os.environ[\"RALPH_MCP_ENDPOINT\"]
prompt = Path.cwd() / \".agent/tmp/rebase_conflict_resolution_prompt.md\"
paths = [
    line.strip()[3:-1]
    for line in prompt.read_text(encoding=\"utf-8\").splitlines()
    if line.strip().startswith(\"- `\") and line.strip().endswith(\"`\")
]
def call(request_id, name, arguments):
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({\"jsonrpc\": \"2.0\", \"id\": request_id, \"method\": \"tools/call\", \"params\": {\"name\": name, \"arguments\": arguments}}).encode(),
        headers={\"Content-Type\": \"application/json\"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode()
        if \"\\\"error\\\"\" in body:
            raise RuntimeError(body)
for path in paths:
    call(1, \"write_file\", {\"path\": path, \"content\": \"resolved by real MCP agent\\n\"})
call(2, \"mcp__ralph__declare_complete\", {\"summary\": \"resolved conflict\"})
print(\"resolution complete\")
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@lru_cache(maxsize=1)
def _policy_bundle():
    return load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")


def _resolution_policy():
    bundle = _policy_bundle()
    chain = bundle.agents.agent_chains["rebase_conflict_resolution"].model_copy(
        update={"agents": ["claude"]}
    )
    agents = bundle.agents.model_copy(
        update={"agent_chains": {**bundle.agents.agent_chains, "rebase_conflict_resolution": chain}}
    )
    return bundle.model_copy(update={"agents": agents})


def _config(command: Path) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_fetch_enabled": False,
            },
            "agents": {
                "claude": {
                    "cmd": str(command),
                    "transport": AgentTransport.AGY.value,
                }
            },
        }
    )


def test_auto_integrate_regression_live_agent_resolves_conflict_through_mcp(
    tmp_path: Path,
) -> None:
    """Step 7 / AC-01..AC-04: a live MCP session clears conflict markers."""
    shared = tmp_path / "shared.txt"
    shared.write_text(
        "<<<<<<< HEAD\ntarget\n=======\nfeature\n>>>>>>> feature\n",
        encoding="utf-8",
    )
    config = _config(_write_mcp_agent(tmp_path))
    scope = WorkspaceScope(tmp_path)
    registry = AgentRegistry.from_config(config)
    display_context = make_display_context()
    display = MagicMock()
    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=_resolution_policy(),
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=PipelineDeps(display_context=display_context),
        workspace_scope=scope,
        display_context=display_context,
    )

    resolved = resolver(
        tmp_path,
        "main",
        RebaseStop(
            sha="a" * 40,
            subject="feature edit",
            conflicted_files=("shared.txt",),
            stop_index=1,
            stop_cap=10,
            replay_index=1,
            replay_total=1,
        ),
    )

    assert resolved is True
    assert shared.read_text(encoding="utf-8") == "resolved by real MCP agent\n"
