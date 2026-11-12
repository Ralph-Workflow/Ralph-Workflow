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


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_enable_detected_agents_activates_only_detected_and_is_idempotent": (
        "the retired comment-fenced agent blocks no longer activate, so the fixture "
        "promotes a detected agent through its structured [inactive_agents.<name>] "
        "table and keeps a fenced block inert; the check still asserts that only a "
        "detected agent is activated and that the call is idempotent"
    ),
    "test_enable_detected_agents_keeps_comment_fenced_agent_blocks_inert": (
        "a comment-fenced agent block is no longer configuration, so the check now "
        "asserts that the fenced region stays inert, is not activated, and leaves no "
        "backup behind"
    ),
}


def test_enable_detected_agents_activates_only_detected_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "ralph-workflow.toml"
    config.write_text(
        '[agents.claude]\ncmd = "custom-claude"\n\n'
        '[inactive_agents.codex]\ncmd = "codex exec"\n\n'
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


def test_enable_detected_agents_promotes_inactive_agent_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "ralph-workflow-agents.toml"
    config.write_text('[inactive_agents.codex]\ncmd = "codex exec"\n', encoding="utf-8")
    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    assert enable_detected_agents(config) == ["codex"]
    assert config.read_text(encoding="utf-8") == '[agents.codex]\ncmd = "codex exec"\n'


def test_enable_detected_agents_keeps_comment_fenced_agent_blocks_inert(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "ralph-workflow-agents.toml"
    initial_content = (
        '# @AGENT-BLOCK-START: codex\n# [agents.codex]\n# cmd = "codex exec"\n# @AGENT-BLOCK-END\n'
    )
    config.write_text(initial_content, encoding="utf-8")
    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    assert enable_detected_agents(config) == []
    assert config.read_text(encoding="utf-8") == initial_content
    assert not config.with_name(f"{config.name}.bak").exists()


def test_autowire_chains_regression_replaces_pristine_defaults_for_detected_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.config.agent_detection import autowire_chains_to_detected_agent

    defaults = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    config = tmp_path / "ralph-workflow.toml"
    original = (defaults / "ralph-workflow.toml").read_text(encoding="utf-8")
    config.write_text(original, encoding="utf-8")
    monkeypatch.setattr("ralph.config.agent_detection.shutil.which", lambda _: None)

    assert autowire_chains_to_detected_agent(config, detected=["codex"]) == ["claude"]

    rewritten = config.read_text(encoding="utf-8")
    for chain in ("planning", "development", "analysis", "commit"):
        assert f'{chain} = ["codex"]' in rewritten
    assert rewritten.replace('["codex"]', '["claude/opus"]', 1) != original


def test_autowire_chains_regression_preserves_user_customized_chains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.config.agent_detection import autowire_chains_to_detected_agent

    defaults = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    config = tmp_path / "ralph-workflow.toml"
    original = (defaults / "ralph-workflow.toml").read_text(encoding="utf-8")
    customized = original.replace('development = ["claude/sonnet"]', 'development = ["pi"]')
    config.write_text(customized, encoding="utf-8")
    monkeypatch.setattr("ralph.config.agent_detection.shutil.which", lambda _: None)

    assert autowire_chains_to_detected_agent(config, detected=["codex"]) == "chains-customized"
    assert config.read_text(encoding="utf-8") == customized
