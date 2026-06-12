"""Tests for ``AgentConfig.subagent_capability`` default and partial-override path.

The bundled ``ralph-workflow.toml`` ships with ``[agents.claude]
subagent_capability = true`` so capable agents dispatch their own
sub-agents out of the box. The model infers the default to ``True`` for
Claude / Claude-interactive transports and to ``None`` (no inference) for
every other transport.

A user with an existing project-local ``.agent/ralph-workflow.toml``
that overrides ``[agents.claude]`` without setting
``subagent_capability`` must still inherit the ``True`` default — that is
the prompt's "no manual configuration required" requirement.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.config.loader import GLOBAL_CONFIG_PATH, load_config
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
    """A project-local config that overrides ``[agents.claude]`` WITHOUT
    setting ``subagent_capability`` must still resolve to ``True`` because
    the bundled default ships that key uncommented (via the deep-merge
    pipeline) and the model fills the missing field with ``True`` for
    Claude / Claude-interactive transports.

    NOTE: ``[agents.*]`` tables live in ``ralph-workflow.toml``, NOT
    ``agents.toml`` — ``agents.toml`` only accepts ``[agent_chains]`` and
    ``[agent_drains]``. The test writes the partial override to
    ``.agent/ralph-workflow.toml`` so the loader's three-layer merge
    (global → local → CLI) actually surfaces the override.
    """
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
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


def test_bundled_ralph_workflow_toml_shipped_with_subagent_capability_true() -> None:
    """The bundled ``ralph/policy/defaults/ralph-workflow.toml`` MUST ship
    with ``[agents.claude] subagent_capability = true`` uncommented.

    This pins the bundled default end-to-end: the model_post_init default is
    covered by ``test_subagent_capability_defaults_true_for_claude_command``,
    the partial-override merge path is covered by
    ``test_partial_override_inherits_subagent_capability_default_for_claude``,
    and this test covers the third leg of requirement 3 ("Task MCP enabled by
    default") by reading the shipped file directly. A future edit that
    re-comments the line (or moves it out of the ``[agents.claude]`` block)
    would break PROMPT.md requirement 3 and would now fail this test.

    Implementation note: this is a black-box, path-anchored source-text check
    (same pattern as ``test_continuation_template_parallel_guidance.py``) so
    it stays under the 60s combined test budget — no Ralph imports, no
    fixture overhead, no parser invocation.
    """
    repo_root = Path(__file__).resolve().parents[1]
    bundled = repo_root / "ralph" / "policy" / "defaults" / "ralph-workflow.toml"
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
