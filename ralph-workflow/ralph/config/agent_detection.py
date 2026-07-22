"""Enable bundled agent configuration blocks for CLIs found on PATH."""

from __future__ import annotations

import os
import re
import shutil
from typing import TYPE_CHECKING, cast

from ralph.agents.builtin import builtin_supports
from ralph.config.bootstrap import resolve_global_config_dir

if TYPE_CHECKING:
    from pathlib import Path


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


def enable_detected_agents(config_path: Path | None = None) -> list[str]:
    """Activate untouched bundled blocks for installed agents, without changing active ones."""
    path = config_path or resolve_global_config_dir() / "ralph-workflow.toml"
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
        content = cast("str", match.group("content"))
        uncommented = "\n".join(
            line[2:] if line.startswith("# ") else line[1:] if line.startswith("#") else line
            for line in content.splitlines()
        )
        text = text[: match.start()] + uncommented + "\n" + text[match.end() :]
        enabled.append(name)

    if enabled:
        path.write_text(text, encoding="utf-8")
    return enabled
