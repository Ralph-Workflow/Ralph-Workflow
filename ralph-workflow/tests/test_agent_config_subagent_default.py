from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.config.loader import (
    GLOBAL_AGENTS_CONFIG_PATH,
    GLOBAL_CONFIG_PATH,
    load_config,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _scope_for(path: Path) -> WorkspaceScope:
    return WorkspaceScope(path)


def test_subagent_capability_defaults_true_for_claude_command() -> None:
    config = AgentConfig(cmd="claude")
    assert config.subagent_capability is True
    assert config.transport is not None
    assert config.transport.value == "claude_interactive"


def test_subagent_capability_defaults_true_for_claude_transport() -> None:
    config = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE)
    assert config.subagent_capability is True


def test_subagent_capability_explicit_false_is_respected() -> None:
    config = AgentConfig(cmd="claude", subagent_capability=False)
    assert config.subagent_capability is False


def test_subagent_capability_stays_none_for_opencode() -> None:
    config = AgentConfig(cmd="opencode")
    assert config.subagent_capability is None


def test_subagent_capability_stays_none_for_codex() -> None:
    config = AgentConfig(cmd="codex")
    assert config.subagent_capability is None


def test_subagent_capability_stays_none_for_nanocoder() -> None:
    config = AgentConfig(cmd="nanocoder")
    assert config.subagent_capability is None


def test_subagent_capability_stays_none_for_agy() -> None:
    config = AgentConfig(cmd="agy")
    assert config.subagent_capability is None


def test_subagent_capability_stays_none_for_explicit_claude_interactive_false_override() -> None:
    config = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        subagent_capability=False,
    )
    assert config.subagent_capability is False


def test_partial_override_inherits_subagent_capability_default_for_claude(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_AGENTS_CONFIG_PATH",
        tmp_path / GLOBAL_AGENTS_CONFIG_PATH.name,
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(
        textwrap.dedent(
            """\
            [agents.claude]
            cmd = "claude"
            transport = "claude_interactive"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))
    assert "claude" in config.agents
    assert config.agents["claude"].subagent_capability is True


def test_bundled_agents_toml_shipped_with_subagent_capability_true() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    bundled = repo_root / "ralph" / "policy" / "defaults" / "ralph-workflow-agents.toml"
    assert bundled.is_file(), f"bundled default not found at {bundled}"

    content = bundled.read_text(encoding="utf-8")

    assert "subagent_capability = true" in content, (
        f"bundled {bundled} is missing the uncommented "
        "`subagent_capability = true` line under [agents.claude]"
    )

    lines = content.splitlines()
    claude_block_lines: list[str] = []
    in_claude_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_claude_block = stripped == "[agents.claude]"
            continue
        if in_claude_block:
            claude_block_lines.append(stripped)

    assert any("subagent_capability = true" in line for line in claude_block_lines), (
        "`subagent_capability = true` must appear inside the [agents.claude] "
        "block of the bundled ralph-workflow.toml, not in some other [agents.*] "
        "block or above all section headers"
    )
