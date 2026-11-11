from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._options import (
    _policy_from_options,
    build_invoke_options_from_config,
)
from ralph.config.general_config import GeneralConfig
from ralph.timeout_defaults import DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS


def test_default_weights_match_documented_policy() -> None:
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS == {
        "source": 1.0,
        "log": 0.0,
        "cache": 0.0,
        "artifact": 0.0,
        "other": 0.0,
    }


def test_default_weights_all_binary() -> None:
    for value in DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS.values():
        assert value in {0.0, 1.0}


def test_timeout_policy_default_weights_match_documented_policy() -> None:
    policy = TimeoutPolicy(idle_timeout_seconds=0.1)
    assert policy.workspace_change_weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_timeout_policy_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="not_a_kind"):
        TimeoutPolicy(
            idle_timeout_seconds=0.1,
            workspace_change_weights={"not_a_kind": 1.0},
        )


def test_timeout_policy_rejects_intermediate_weight() -> None:
    with pytest.raises(ValueError, match="binary"):
        TimeoutPolicy(
            idle_timeout_seconds=0.1,
            workspace_change_weights={"source": 0.5},
        )


def test_timeout_policy_rejects_weight_above_one() -> None:
    with pytest.raises(ValueError, match="binary"):
        TimeoutPolicy(
            idle_timeout_seconds=0.1,
            workspace_change_weights={"source": 2.0},
        )


def test_timeout_policy_accepts_partial_overrides() -> None:
    policy = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        workspace_change_weights={"source": 1.0, "log": 1.0},
    )
    assert policy.workspace_change_weights == {"source": 1.0, "log": 1.0}


def test_general_config_default_weights() -> None:
    config = GeneralConfig()
    assert config.agent_workspace_change_weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_general_config_accepts_partial_override() -> None:
    config = GeneralConfig(agent_workspace_change_weights={"source": 1.0, "log": 1.0})
    assert config.agent_workspace_change_weights == {"source": 1.0, "log": 1.0}


def test_general_config_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="not_a_kind"):
        GeneralConfig(agent_workspace_change_weights={"not_a_kind": 1.0})


def test_general_config_rejects_intermediate_weight() -> None:
    with pytest.raises(ValueError, match="binary"):
        GeneralConfig(agent_workspace_change_weights={"source": 0.5})


def test_general_config_field_description_mentions_all_kinds() -> None:
    config = GeneralConfig()
    description = config.model_fields["agent_workspace_change_weights"].description
    assert description is not None
    for kind in ("source", "log", "cache", "artifact", "other"):
        assert kind in description, (
            f"field description must mention WorkspaceChangeKind {kind!r}, got: {description!r}"
        )

    assert "binary" in description.lower() or "0.0" in description
    assert "1.0" in description


def test_general_config_to_invoke_options_threads_field() -> None:
    config = GeneralConfig(
        agent_workspace_change_weights={"source": 1.0, "log": 1.0},
    )
    opts = build_invoke_options_from_config(config)
    policy = _policy_from_options(opts)
    assert policy.workspace_change_weights == {"source": 1.0, "log": 1.0}
