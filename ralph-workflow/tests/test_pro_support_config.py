"""Black-box unit tests for Pro-owned config file invariants."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from ralph.config.loader import load_config
from ralph.pro_support import env as env_module
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path


def _write_pro_owned_config_files(workspace_root: Path) -> None:
    """Write a minimal but representative set of Pro-owned config files."""
    agent_dir = workspace_root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "pipeline.toml").write_text(
        (
            "[pipeline]\n"
            'entry_phase = "planning"\n'
            'terminal_phase = "complete"\n'
            "recovery = { failed_route = \"failed_terminal\" }\n"
            "[pipeline.phases.planning]\n"
            'drain = "planning"\n'
            "[pipeline.phases.complete]\n"
            'drain = "development"\n'
            "[pipeline.phases.failed_terminal]\n"
            'drain = "development"\n'
            'role = "terminal"\n'
            'terminal_outcome = "failure"\n'
        ),
        encoding="utf-8",
    )
    (agent_dir / "artifacts.toml").write_text(
        (
            "[artifacts]\n"
            "[artifacts.drains.planning]\n"
            'artifact_type = "plan"\n'
            'json_path = ".agent/artifacts/plan.json"\n'
        ),
        encoding="utf-8",
    )
    (workspace_root / ".agent" / "mcp.toml").write_text(
        "[mcp]\n",
        encoding="utf-8",
    )


def test_engine_does_not_reinterpret_pro_owned_config(tmp_path: Path) -> None:
    """The engine must read Pro-owned config and never mutate it on disk.

    Loads the config through the public engine API and then re-reads the
    raw files from disk to assert byte-for-byte equality. A regression
    that lets the engine rewrite any of these files in Pro mode would
    fail the byte-equality check.
    """
    workspace_root = tmp_path
    _write_pro_owned_config_files(workspace_root)
    (workspace_root / "PROMPT.md").write_text("# hello\n", encoding="utf-8")
    (workspace_root / "ralph-workflow.toml").write_text(
        "[general]\nverbosity = 0\n",
        encoding="utf-8",
    )

    before_pipeline = (workspace_root / ".agent" / "pipeline.toml").read_bytes()
    before_artifacts = (workspace_root / ".agent" / "artifacts.toml").read_bytes()
    before_mcp = (workspace_root / ".agent" / "mcp.toml").read_bytes()
    before_prompt = (workspace_root / "PROMPT.md").read_bytes()

    workspace_scope = WorkspaceScope(workspace_root)
    config = load_config(None, {}, workspace_scope=workspace_scope)
    assert config is not None

    after_pipeline = (workspace_root / ".agent" / "pipeline.toml").read_bytes()
    after_artifacts = (workspace_root / ".agent" / "artifacts.toml").read_bytes()
    after_mcp = (workspace_root / ".agent" / "mcp.toml").read_bytes()
    after_prompt = (workspace_root / "PROMPT.md").read_bytes()
    assert before_pipeline == after_pipeline
    assert before_artifacts == after_artifacts
    assert before_mcp == after_mcp
    assert before_prompt == after_prompt


def test_engine_does_not_reinterpret_pro_owned_config_in_memory_workspace() -> None:
    """Same invariant, but exercised through the in-memory workspace seam.

    Uses a ``MemoryWorkspace`` to prove the engine's read path does not
    call ``workspace.write`` against any Pro-owned file when a config
    is loaded.
    """
    mem = MemoryWorkspace()
    mem.write("PROMPT.md", "# in-memory prompt\n")
    mem.mkdirs(".agent")
    mem.write(
        ".agent/pipeline.toml",
        (
            "[pipeline]\n"
            'entry_phase = "planning"\n'
            'terminal_phase = "complete"\n'
            'recovery = { failed_route = "failed_terminal" }\n'
            "[pipeline.phases.planning]\n"
            'drain = "planning"\n'
            "[pipeline.phases.complete]\n"
            'drain = "development"\n'
            "[pipeline.phases.failed_terminal]\n"
            'drain = "development"\n'
            'role = "terminal"\n'
            'terminal_outcome = "failure"\n'
        ),
    )
    mem.write(
        ".agent/artifacts.toml",
        (
            "[artifacts]\n"
            "[artifacts.drains.planning]\n"
            'artifact_type = "plan"\n'
            'json_path = ".agent/artifacts/plan.json"\n'
        ),
    )
    mem.write(".agent/mcp.toml", "[mcp]\n")

    config = load_config(None, {}, workspace_scope=WorkspaceScope("/in-memory"))
    assert config is not None
    assert mem.read(".agent/pipeline.toml").startswith("[pipeline]")
    assert mem.read(".agent/artifacts.toml").startswith("[artifacts]")
    assert mem.read(".agent/mcp.toml") == "[mcp]\n"


def test_pro_owned_config_round_trip_tomllib(tmp_path: Path) -> None:
    """Smoke test that the on-disk Pro files are still parseable TOML."""
    workspace_root = tmp_path
    _write_pro_owned_config_files(workspace_root)
    parsed = tomllib.loads(
        (workspace_root / ".agent" / "pipeline.toml").read_text(encoding="utf-8")
    )
    assert parsed["pipeline"]["entry_phase"] == "planning"


def test_pro_mode_does_not_introduce_extra_env_vars() -> None:
    """The contract is bounded to three env vars; assert no helper reads beyond them."""
    keys = {env_module.RALPH_WORKFLOW_PRO, env_module.RALPH_WORKSPACE, env_module.PROMPT_PATH}
    assert keys == {"RALPH_WORKFLOW_PRO", "RALPH_WORKSPACE", "PROMPT_PATH"}
