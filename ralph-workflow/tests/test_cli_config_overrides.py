"""CLI flag overrides must reach the configuration the run actually uses.

Two regressions pinned here:

* ``--developer-agent`` / ``--developer-model`` were emitted as top-level
  override keys that ``UnifiedConfig`` silently dropped, so the flags never
  changed which agent ran the development phase.
* Every run emitted ``None`` for ``general.git_user_name`` /
  ``general.git_user_email`` when the flags were absent, and the override
  merge let that ``None`` erase the identity configured in the TOML file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.chain import ChainManager
from ralph.cli.main import CLIOverrideInput, build_cli_overrides
from ralph.config.loader import load_config
from ralph.policy.loader import load_agents_policy
from ralph.policy.validation import PolicyValidationError

if TYPE_CHECKING:
    from pathlib import Path

_CHAINS_AND_DRAINS = (
    "[agent_chains]\n"
    'planning = ["claude/opus"]\n'
    'development = ["claude/sonnet", "opencode"]\n'
    'analysis = ["claude/opus"]\n'
    'commit = ["claude/haiku"]\n'
    "\n"
    "[agent_drains]\n"
    'planning = "planning"\n'
    'planning_analysis = "analysis"\n'
    'development = "development"\n'
    'development_analysis = "analysis"\n'
    'development_commit = "commit"\n'
    'analysis = "analysis"\n'
    'commit = "commit"\n'
)


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / ".agent" / "ralph-workflow.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(body, encoding="utf-8")
    return config_path


def _development_agents(config_path: Path, override: CLIOverrideInput) -> list[str]:
    config = load_config(config_path=config_path, cli_overrides=build_cli_overrides(override))
    policy = load_agents_policy(config_path.parent, config=config)
    return list(ChainManager(policy).chain_for_drain("development").agents)


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (CLIOverrideInput(developer_agent="codex"), ["codex"]),
        (CLIOverrideInput(developer_agent="codex", developer_model="gpt-5"), ["codex/gpt-5"]),
        (CLIOverrideInput(developer_model="opus"), ["claude/opus"]),
        (CLIOverrideInput(), ["claude/sonnet", "opencode"]),
    ],
)
def test_developer_flags_select_the_development_agent(
    tmp_path: Path, override: CLIOverrideInput, expected: list[str]
) -> None:
    """The development drain must run the agent/model the CLI flags name."""
    config_path = _write_config(tmp_path, _CHAINS_AND_DRAINS)

    assert _development_agents(config_path, override) == expected


def test_developer_agent_flag_applies_to_the_bundled_agents_policy(tmp_path: Path) -> None:
    """A config without its own chains still honours the flag over the bundled policy."""
    config_path = _write_config(tmp_path, "[general]\n")

    agents = _development_agents(config_path, CLIOverrideInput(developer_agent="codex"))

    assert agents == ["codex"]


def test_developer_agent_flag_leaves_other_drains_on_their_own_chains(tmp_path: Path) -> None:
    """Only the developer role moves; analysis and commit keep their configured agents."""
    config_path = _write_config(tmp_path, _CHAINS_AND_DRAINS)
    config = load_config(
        config_path=config_path,
        cli_overrides=build_cli_overrides(CLIOverrideInput(developer_agent="codex")),
    )
    chains = ChainManager(load_agents_policy(config_path.parent, config=config))

    assert chains.chain_for_drain("development_analysis").agents == ["claude/opus"]
    assert chains.chain_for_drain("commit").agents == ["claude/haiku"]


def test_developer_flags_are_recorded_on_the_config_for_run_start(tmp_path: Path) -> None:
    """The run-start orientation reads the requested developer agent off the config."""
    config_path = _write_config(tmp_path, _CHAINS_AND_DRAINS)
    config = load_config(
        config_path=config_path,
        cli_overrides=build_cli_overrides(
            CLIOverrideInput(developer_agent="codex", developer_model="gpt-5")
        ),
    )

    assert (config.developer_agent, config.developer_model) == ("codex", "gpt-5")


def test_blank_developer_agent_flag_fails_loudly(tmp_path: Path) -> None:
    """An empty agent name must not be silently replaced by the configured chain."""
    config_path = _write_config(tmp_path, _CHAINS_AND_DRAINS)
    config = load_config(
        config_path=config_path,
        cli_overrides=build_cli_overrides(CLIOverrideInput(developer_agent="  ")),
    )

    with pytest.raises(PolicyValidationError, match="--developer-agent"):
        load_agents_policy(config_path.parent, config=config)


def test_developer_agent_key_in_a_config_file_is_reported_and_ignored(tmp_path: Path) -> None:
    """The developer pin is a CLI flag, not a config key: a TOML copy must not apply."""
    config_path = _write_config(tmp_path, 'developer_agent = "codex"\n' + _CHAINS_AND_DRAINS)
    warnings: list[str] = []

    config = load_config(config_path=config_path, unknown_field_warning=warnings.append)

    assert config.developer_agent is None
    assert any("developer_agent" in line for line in warnings)


def test_absent_git_identity_flags_keep_the_configured_identity(tmp_path: Path) -> None:
    """Running without --git-user-name/--git-user-email must not erase the config values."""
    config_path = _write_config(
        tmp_path,
        '[general]\ngit_user_name = "Alice Config"\ngit_user_email = "alice@example.com"\n',
    )

    config = load_config(
        config_path=config_path, cli_overrides=build_cli_overrides(CLIOverrideInput())
    )

    assert config.general.git_user_name == "Alice Config"
    assert config.general.git_user_email == "alice@example.com"


def test_git_identity_flags_still_override_the_configured_identity(tmp_path: Path) -> None:
    """An explicit flag keeps winning over the config file."""
    config_path = _write_config(
        tmp_path,
        '[general]\ngit_user_name = "Alice Config"\ngit_user_email = "alice@example.com"\n',
    )

    config = load_config(
        config_path=config_path,
        cli_overrides=build_cli_overrides(
            CLIOverrideInput(git_user_name="Bob Flag", git_user_email="bob@example.com")
        ),
    )

    assert config.general.git_user_name == "Bob Flag"
    assert config.general.git_user_email == "bob@example.com"
