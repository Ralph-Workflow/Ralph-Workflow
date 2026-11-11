from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._options import (
    _policy_from_options,
    build_invoke_options_from_config,
)
from ralph.config.general_config import GeneralConfig
from ralph.timeout_defaults import (
    NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS,
    NO_PROGRESS_QUIET_SECONDS,
)


def test_default_heartbeat_ceiling_le_default_no_progress_quiet_constant() -> None:
    assert NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS is not None
    assert NO_PROGRESS_QUIET_SECONDS is not None
    assert NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS <= NO_PROGRESS_QUIET_SECONDS, (
        f"default NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS"
        f" ({NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS}) MUST be <="
        f" default NO_PROGRESS_QUIET_SECONDS ({NO_PROGRESS_QUIET_SECONDS})"
        f" so the cross-field validator accepts a default constructor"
    )


def test_general_config_default_heartbeat_ceiling() -> None:
    config = GeneralConfig()
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == (
        NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS
    )


def test_general_config_rejects_heartbeat_ceiling_above_no_progress_quiet_seconds() -> None:
    with pytest.raises(
        ValueError,
        match="agent_no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        GeneralConfig(
            agent_no_progress_quiet_seconds=120.0,
            agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )


def test_general_config_accepts_heartbeat_ceiling_equal_to_no_progress_quiet_seconds() -> None:
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=240.0,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == 240.0
    assert config.agent_no_progress_quiet_seconds == 240.0


def test_general_config_accepts_heartbeat_ceiling_below_no_progress_quiet_seconds() -> None:
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=240.0,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=180.0,
    )
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == 180.0
    assert config.agent_no_progress_quiet_seconds == 240.0


def test_general_config_accepts_heartbeat_ceiling_with_none_no_progress_quiet_seconds() -> None:
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=None,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert config.agent_no_progress_quiet_seconds is None
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == 240.0


def test_general_config_accepts_heartbeat_ceiling_none() -> None:
    config = GeneralConfig(
        agent_no_progress_quiet_heartbeat_ceiling_seconds=None,
    )
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds is None


def test_general_config_field_description_mentions_constraint() -> None:
    config = GeneralConfig()
    description = config.model_fields[
        "agent_no_progress_quiet_heartbeat_ceiling_seconds"
    ].description
    assert description is not None
    assert "agent_no_progress_quiet_seconds" in description, (
        f"field description must mention the cross-field constraint with"
        f" agent_no_progress_quiet_seconds, got: {description!r}"
    )
    assert "SHORTER" in description or "shorter" in description or "BEFORE" in description, (
        f"field description must explain WHY the constraint exists"
        f" (heartbeat-only is a SHORTER ceiling that fires BEFORE the"
        f" dumb-kill ceiling), got: {description!r}"
    )


def test_general_config_heartbeat_ceiling_threads_through_to_timeout_policy() -> None:
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=240.0,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=180.0,
    )
    opts = build_invoke_options_from_config(config)
    policy = _policy_from_options(opts)
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds == 180.0
    assert policy.no_progress_quiet_seconds == 240.0


def test_general_config_rejects_invalid_combination_round_trip() -> None:
    with pytest.raises(
        ValueError,
        match="agent_no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        GeneralConfig(
            agent_no_progress_quiet_seconds=120.0,
            agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )


def test_timeout_policy_validator_mirrors_general_config_validator() -> None:
    with pytest.raises(
        ValueError,
        match="no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )

    with pytest.raises(
        ValueError,
        match="agent_no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        GeneralConfig(
            agent_no_progress_quiet_seconds=120.0,
            agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )
