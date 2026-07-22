"""Behavior tests for built-in-agent discovery during init."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ralph.config.agent_detection import detect_installed_agents, enable_detected_agents

if TYPE_CHECKING:
    import pytest


def test_detect_installed_agents_returns_only_path_binaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan step 4: discovery checks each built-in command's executable."""
    monkeypatch.setattr(
        "ralph.config.agent_detection.builtin_supports",
        lambda: (
            SimpleNamespace(name="claude", cmd="claude"),
            SimpleNamespace(name="codex", cmd="codex exec"),
        ),
    )

    def fake_which(binary: str) -> str | None:
        return "/tools/claude" if binary == "claude" else None

    monkeypatch.setattr("ralph.config.agent_detection.shutil.which", fake_which)

    assert detect_installed_agents() == ["claude"]


def test_enable_detected_agents_activates_only_detected_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plan step 4: detected untouched blocks are activated once without editing active agents."""
    config = tmp_path / "ralph-workflow.toml"
    config.write_text(
        '[agents.claude]\ncmd = "custom-claude"\n\n'
        '# @AGENT-BLOCK-START: codex\n# [agents.codex]\n# cmd = "codex exec"\n'
        "# @AGENT-BLOCK-END\n\n"
        '# @AGENT-BLOCK-START: pi\n# [agents.pi]\n# cmd = "pi"\n# @AGENT-BLOCK-END\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ralph.config.agent_detection.detect_installed_agents", lambda: ["claude", "codex"]
    )

    assert enable_detected_agents(config) == ["codex"]
    assert config.read_text(encoding="utf-8") == (
        '[agents.claude]\ncmd = "custom-claude"\n\n'
        '[agents.codex]\ncmd = "codex exec"\n\n'
        '# @AGENT-BLOCK-START: pi\n# [agents.pi]\n# cmd = "pi"\n# @AGENT-BLOCK-END\n'
    )
    assert enable_detected_agents(config) == []
