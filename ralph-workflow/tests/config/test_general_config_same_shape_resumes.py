"""Pin the agent unproductive limits config wiring.

Tests for the ``[general] agent_max_consecutive_unproductive`` and
``[general] agent_max_total_unproductive`` fields:

  (a) The field defaults equal ``AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT`` and
      ``AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT``.
  (b) TOML overrides round-trip through ``load_config()``.
  (c) Values < 1 are rejected by the validator with clear field-name errors.
  (d) The configuration reaches ``RecoveryControllerOptions``.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):
  - No real subprocess.
  - No real filesystem beyond ``tmp_path`` for the TOML round-trip.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_config
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.timeout_defaults import (
    AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT,
    AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT,
)


def test_general_config_defaults() -> None:
    """GeneralConfig unproductive limits default to the expected defaults."""
    config = GeneralConfig()
    assert (
        config.agent_max_consecutive_unproductive
        == AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
    )
    assert (
        config.agent_max_total_unproductive
        == AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT
    )
    assert AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT == 3
    assert AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT == 9


def test_general_config_toml_override_round_trips_through_load_config(
    tmp_path: Path,
) -> None:
    """TOML overrides round-trip to the config fields."""
    config_file = tmp_path / "ralph-workflow.toml"
    config_file.write_text(
        "[general]\nagent_max_consecutive_unproductive = 5\nagent_max_total_unproductive = 15\n",
        encoding="utf-8",
    )

    config = load_config(config_path=config_file)
    assert config.general.agent_max_consecutive_unproductive == 5
    assert config.general.agent_max_total_unproductive == 15


def test_general_config_default_when_toml_key_absent(tmp_path: Path) -> None:
    """No unproductive keys keep the defaults."""
    config_file = tmp_path / "ralph-workflow.toml"
    config_file.write_text("[general]\n", encoding="utf-8")

    config = load_config(config_path=config_file)
    assert (
        config.general.agent_max_consecutive_unproductive
        == AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
    )
    assert (
        config.general.agent_max_total_unproductive
        == AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT
    )


def test_general_config_rejects_zero_and_negative_with_field_name_in_error() -> None:
    """Values < 1 are rejected by the validator with clear field-name errors."""
    with pytest.raises(ValueError) as excinfo:
        GeneralConfig(agent_max_consecutive_unproductive=0)
    assert "agent_max_consecutive_unproductive" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        GeneralConfig(agent_max_consecutive_unproductive=-2)
    assert "agent_max_consecutive_unproductive" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        GeneralConfig(agent_max_total_unproductive=0)
    assert "agent_max_total_unproductive" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        GeneralConfig(agent_max_total_unproductive=-1)
    assert "agent_max_total_unproductive" in str(excinfo.value)


def test_general_config_to_recovery_controller_options() -> None:
    """RecoveryControllerOptions carries the unproductive limits."""
    options = RecoveryControllerOptions(
        agent_max_consecutive_unproductive=5,
        agent_max_total_unproductive=12,
    )
    assert options.agent_max_consecutive_unproductive == 5
    assert options.agent_max_total_unproductive == 12
