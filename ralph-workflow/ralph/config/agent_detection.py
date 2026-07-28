"""Enable bundled agent configuration blocks for CLIs found on PATH."""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path
from re import Match
from typing import cast

from ralph.agents.builtin import builtin_supports
from ralph.config.bootstrap import resolve_global_config_dir


def _binary_for(name: str, cmd: str) -> str:
    """Return the PATH binary for a built-in command, honoring documented overrides."""
    override_name = {"agy": "RALPH_AGY_BINARY", "cursor": "RALPH_CURSOR_BINARY"}.get(name)
    override = os.environ.get(override_name) if override_name is not None else None
    return (override or cmd).split(maxsplit=1)[0]


def detect_installed_agents() -> list[str]:
    """Return built-in agent names whose command binary is available on PATH."""
    return [
        support.name
        for support in builtin_supports()
        if shutil.which(_binary_for(support.name, support.cmd)) is not None
    ]


def _agent_chains_from_toml(text: str) -> dict[str, list[str]]:
    """Read the flat chain mapping from a bundled or user main config."""
    parsed = cast("dict[str, object]", tomllib.loads(text))
    raw_chains = parsed.get("agent_chains")
    if not isinstance(raw_chains, dict):
        return {}
    return {
        name: entries
        for name, entries in cast("dict[str, object]", raw_chains).items()
        if isinstance(entries, list) and all(isinstance(entry, str) for entry in entries)
    }


def autowire_chains_to_detected_agent(
    main_config_path: Path, *, detected: list[str] | None = None
) -> list[str] | None:
    """Point untouched default chains at a detected CLI when Claude is unavailable."""
    defaults_path = Path(__file__).parents[1] / "policy" / "defaults" / "ralph-workflow.toml"
    text = main_config_path.read_text(encoding="utf-8")
    default_text = defaults_path.read_text(encoding="utf-8")
    default_chains = _agent_chains_from_toml(default_text)
    if _agent_chains_from_toml(text) != default_chains:
        return None

    supports = {support.name: support for support in builtin_supports()}
    default_agents = {
        entry.split("/", 1)[0]
        for entries in default_chains.values()
        for entry in entries
    }
    if any(
        support is not None and shutil.which(_binary_for(name, support.cmd)) is not None
        for name in default_agents
        if (support := supports.get(name)) is not None
    ):
        return None

    selected = (detected if detected is not None else detect_installed_agents())
    if not selected:
        return None
    chain_block = re.compile(r"(?ms)^\[agent_chains\]\n.*?(?=^\[|\Z)")
    match = chain_block.search(text)
    if match is None:
        return None
    def replacement(item: Match[str]) -> str:
        return f'{item.group(1)} = ["{selected[0]}"]'

    rewritten = re.sub(
        r"(?m)^(planning|development|analysis|commit)\s*=\s*\[[^\]]*\]$",
        replacement,
        match.group(),
    )
    main_config_path.write_text(text[: match.start()] + rewritten + text[match.end() :], encoding="utf-8")
    return sorted(default_agents)


def enable_detected_agents(config_path: Path | None = None) -> list[str]:
    """Activate untouched bundled blocks for installed agents, without changing active ones."""
    path = config_path or resolve_global_config_dir() / "ralph-workflow-agents.toml"
    text = path.read_text(encoding="utf-8")
    enabled: list[str] = []

    for name in detect_installed_agents():
        header = re.compile(rf"^\s*\[agents\.{re.escape(name)}\]\s*$", re.MULTILINE)
        if header.search(text):
            continue
        block = re.compile(
            rf"^# @AGENT-BLOCK-START: {re.escape(name)}\n"
            rf"(?P<content>.*?)"
            rf"^# @AGENT-BLOCK-END\n?",
            re.MULTILINE | re.DOTALL,
        )
        match = block.search(text)
        if match is None:
            continue
        content = cast(
            "str", match.group("content")
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        uncommented = "\n".join(
            line[2:] if line.startswith("# ") else line[1:] if line.startswith("#") else line
            for line in content.splitlines()
        )
        text = text[: match.start()] + uncommented + "\n" + text[match.end() :]
        enabled.append(name)

    if enabled:
        path.write_text(text, encoding="utf-8")
    return enabled
