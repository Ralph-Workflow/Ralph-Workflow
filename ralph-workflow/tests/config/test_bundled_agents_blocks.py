from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from ralph.config.agent_detection import enable_detected_agents
from ralph.policy.loader import default_dir

AGENTS_TEMPLATE_NAME = "ralph-workflow-agents.toml"
TEMPLATE_PATH = default_dir() / AGENTS_TEMPLATE_NAME
TEMPLATE_TEXT = TEMPLATE_PATH.read_text(encoding="utf-8")

EXPECTED_BLOCK_NAMES = (
    "claude",
    "claude-headless",
    "codex",
    "opencode",
    "nanocoder",
    "agy",
    "pi",
    "cursor",
    "kimi",
)


def _block_names(text: str) -> list[str]:
    return re.findall(r"^\[inactive_agents\.([^\]]+)\]", text, re.MULTILINE)


def _block_content(text: str, name: str) -> str:
    block = re.compile(
        rf"^\[inactive_agents\.{re.escape(name)}\]\n"
        rf"(?P<content>.*?)"
        rf"(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = block.search(text)
    assert match is not None, f"the bundled template has no inactive agent for {name!r}"
    return match.group("content")


def _isolated_block_file(name: str) -> str:
    return f"[inactive_agents.{name}]\n{_block_content(TEMPLATE_TEXT, name)}"


def test_bundled_template_ships_every_expected_agent_block() -> None:
    assert tuple(_block_names(TEMPLATE_TEXT)) == EXPECTED_BLOCK_NAMES


def test_bundled_agents_template_is_valid_toml() -> None:
    data = tomllib.loads(TEMPLATE_TEXT)
    agents = data["agents"]
    assert "claude" in agents
    assert agents["claude"]["cmd"] == "claude"
    inactive = data["inactive_agents"]
    assert tuple(inactive.keys()) == EXPECTED_BLOCK_NAMES


@pytest.mark.parametrize("name", EXPECTED_BLOCK_NAMES)
def test_every_bundled_agent_block_is_valid_toml(name: str) -> None:
    data = tomllib.loads(TEMPLATE_TEXT)
    inactive = data["inactive_agents"]
    assert name in inactive
    assert isinstance(inactive[name], dict)
    assert "cmd" in inactive[name]


@pytest.mark.parametrize("name", EXPECTED_BLOCK_NAMES)
def test_enable_detected_agents_enables_every_bundled_block(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / AGENTS_TEMPLATE_NAME
    config_path.write_text(_isolated_block_file(name), encoding="utf-8")
    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: [name])

    enabled = enable_detected_agents(config_path)

    assert enabled == [name]
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert name in parsed["agents"]
    data = tomllib.loads(TEMPLATE_TEXT)
    assert parsed["agents"][name] == data["inactive_agents"][name]


def test_enable_detected_agents_enables_opencode_from_the_bundled_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / AGENTS_TEMPLATE_NAME
    config_path.write_text(TEMPLATE_TEXT, encoding="utf-8")
    monkeypatch.setattr(
        "ralph.config.agent_detection.detect_installed_agents", lambda: ["opencode"]
    )

    enabled = enable_detected_agents(config_path)

    assert enabled == ["opencode"]
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["agents"]["opencode"] == {
        "cmd": "opencode",
        "transport": "opencode",
        "json_parser": "opencode",
        "session_flag": "--session {}",
        "display_name": "OpenCode",
    }
