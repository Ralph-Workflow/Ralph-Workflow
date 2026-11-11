"""Black-box tests for the bundled agent-block template.

``ralph --init`` enables a detected agent by uncommenting its
``@AGENT-BLOCK-START`` / ``@AGENT-BLOCK-END`` region and parsing the
result with ``tomllib`` (see
``ralph.config.agent_detection.enable_detected_agents``). A region that
carries prose commentary inside the markers uncomments to bare keys with
no ``=``, so the block is rejected as invalid TOML and that agent can
never be enabled. Every bundled region must therefore uncomment to valid
TOML, and the enablement path must accept each one.
"""

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
    """Return every ``@AGENT-BLOCK-START: <name>`` marker name, in file order."""
    return re.findall(r"^# @AGENT-BLOCK-START: (\S+)$", text, re.MULTILINE)


def _block_content(text: str, name: str) -> str:
    """Return the commented body of one agent block.

    Mirrors the block extraction in ``enable_detected_agents``: the body
    is everything between the ``# @AGENT-BLOCK-START: <name>`` marker and
    the ``# @AGENT-BLOCK-END`` marker.
    """
    block = re.compile(
        rf"^# @AGENT-BLOCK-START: {re.escape(name)}\n"
        rf"(?P<content>.*?)"
        rf"^# @AGENT-BLOCK-END\n?",
        re.MULTILINE | re.DOTALL,
    )
    match = block.search(text)
    assert match is not None, f"the bundled template has no @AGENT-BLOCK for {name!r}"
    return match.group("content")


def _uncomment(content: str) -> str:
    """Mirror the uncomment rule of ``enable_detected_agents``.

    Every line of the block body loses one leading ``"# "`` (or ``"#"``);
    anything else is left alone.
    """
    return "\n".join(
        line[2:] if line.startswith("# ") else line[1:] if line.startswith("#") else line
        for line in content.splitlines()
    )


def _isolated_block_file(name: str) -> str:
    """Return a config file that carries exactly one bundled agent block."""
    return (
        f"# @AGENT-BLOCK-START: {name}\n{_block_content(TEMPLATE_TEXT, name)}# @AGENT-BLOCK-END\n"
    )


def test_bundled_template_ships_every_expected_agent_block() -> None:
    """The template ships one ``@AGENT-BLOCK`` region per expected agent."""
    assert tuple(_block_names(TEMPLATE_TEXT)) == EXPECTED_BLOCK_NAMES


def test_bundled_agents_template_is_valid_toml() -> None:
    """The shipped template parses as TOML and still activates claude."""
    data = tomllib.loads(TEMPLATE_TEXT)
    agents = data["agents"]
    assert "claude" in agents
    assert agents["claude"]["cmd"] == "claude"


@pytest.mark.parametrize("name", _block_names(TEMPLATE_TEXT))
def test_every_bundled_agent_block_uncomments_to_valid_toml(name: str) -> None:
    """Every bundled block body uncomments to TOML that ``tomllib`` accepts."""
    uncommented = _uncomment(_block_content(TEMPLATE_TEXT, name))
    try:
        parsed = tomllib.loads(uncommented)
    except tomllib.TOMLDecodeError as exc:
        pytest.fail(
            f"the {name!r} agent block does not uncomment to valid TOML: {exc}\n"
            f"--- uncommented block ---\n{uncommented}"
        )
    assert name in parsed["agents"], f"the {name!r} block does not define [agents.{name}]"


@pytest.mark.parametrize("name", _block_names(TEMPLATE_TEXT))
def test_enable_detected_agents_enables_every_bundled_block(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production enablement path accepts every bundled block."""
    config_path = tmp_path / AGENTS_TEMPLATE_NAME
    config_path.write_text(_isolated_block_file(name), encoding="utf-8")
    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: [name])

    enabled = enable_detected_agents(config_path)

    assert enabled == [name]
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert name in parsed["agents"]
    # The local mirror of the uncomment rule yields the very same
    # key/values the production enablement path writes.
    mirrored = tomllib.loads(_uncomment(_block_content(TEMPLATE_TEXT, name)))
    assert parsed["agents"][name] == mirrored["agents"][name]


def test_enable_detected_agents_enables_opencode_from_the_bundled_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ralph --init`` enables opencode from a copy of the bundled template."""
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
