"""Real-session regression coverage for auto-integration conflict resolution.

This subprocess E2E test launches a deterministic executable through the
production effect executor.  The executable calls Ralph's live MCP endpoint to
edit only the conflicted file and to declare completion; it is deliberately not
a resolver or an ``invoke=`` replacement.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
        timeout=20.0,
    )


def _commit(root: Path, content: str, message: str) -> None:
    (root / "shared.txt").write_text(content, encoding="utf-8")
    _run(root, "add", "shared.txt")
    _run(root, "commit", "-m", message)


def _conflicted_feature(root: Path) -> str:
    target = _run(root, "branch", "--show-current").stdout.strip()
    _commit(root, "seed\n", "seed")
    seed = _run(root, "rev-parse", "HEAD").stdout.strip()
    _run(root, "branch", "feature", seed)
    _run(root, "checkout", "feature")
    _commit(root, "feature\n", "feature edit")
    _run(root, "checkout", target)
    _commit(root, "target\n", "target edit")
    _run(root, "checkout", "feature")
    return target


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


def _config(target: str, command: Path) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
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


def test_auto_integrate_regression_real_agent_resolves_conflicted_rebase_and_fast_forwards(
    tmp_git_repo: Path,
) -> None:
    """Step 7 / AC-01..AC-04: a live MCP session resolves and lands a rebase."""
    target = _conflicted_feature(tmp_git_repo)
    config = _config(target, _write_mcp_agent(tmp_git_repo))
    scope = WorkspaceScope(tmp_git_repo)
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

    result = auto_integrate_after_commit(
        config,
        scope,
        RebaseState(),
        rebase_stop_resolver=resolver,
        display=display,
    )

    assert result is not None
    assert result.last_action == "rebased"
    assert result.fast_forwarded is True
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert branch_sha(tmp_git_repo, target) == head
    assert _run(tmp_git_repo, "log", "--merges", "--oneline").stdout.strip() == ""
    assert not (tmp_git_repo / ".git" / "rebase-merge").exists()
    assert _run(tmp_git_repo, "show", "HEAD:shared.txt").stdout == "resolved by real MCP agent\n"
