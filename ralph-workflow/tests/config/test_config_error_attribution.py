"""A config validation error must name the file the bad value came from."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from loguru import logger

from ralph.config.loader import load_config


def _validation_message(local_config: Path) -> str:
    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR", format="{message}")
    try:
        with pytest.raises(SystemExit):
            load_config(config_path=local_config)
    finally:
        logger.remove(sink_id)
    return "\n".join(records)


def test_bad_agent_definition_in_the_agents_file_names_the_agents_file(tmp_path: Path) -> None:
    """An invalid ``[agents.*]`` value from ralph-workflow-agents.toml must not blame the project file."""
    agents_path = Path(os.environ["XDG_CONFIG_HOME"]) / "ralph-workflow-agents.toml"
    agents_path.parent.mkdir(parents=True, exist_ok=True)
    agents_path.write_text('[agents.myagent]\ncmd = "x"\njson_parser = "bogus"\n', encoding="utf-8")
    local_config = tmp_path / "project-ralph-workflow.toml"
    local_config.write_text("[general]\n", encoding="utf-8")

    message = _validation_message(local_config)

    assert str(agents_path) in message
    assert "project-ralph-workflow.toml" not in message


def test_bad_general_value_in_the_global_file_is_not_blamed_on_cli_flags(tmp_path: Path) -> None:
    """CLI overrides always carry a ``general`` table; that must not capture every general error."""
    global_path = Path(os.environ["XDG_CONFIG_HOME"]) / "ralph-workflow.toml"
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text('[general]\nmax_retries = "many"\n', encoding="utf-8")
    local_config = tmp_path / "project-ralph-workflow.toml"
    local_config.write_text("[general]\n", encoding="utf-8")
    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR", format="{message}")
    try:
        with pytest.raises(SystemExit):
            load_config(
                config_path=local_config,
                cli_overrides={"general": {"execution": {}, "workflow": {}}},
            )
    finally:
        logger.remove(sink_id)

    message = "\n".join(records)
    assert str(global_path) in message
    assert "project-ralph-workflow.toml" not in message
