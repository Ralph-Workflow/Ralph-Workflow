"""Black-box tests for the new ``agent_workspace_change_weights`` config field.

These tests cover three layers:
  - ``TimeoutPolicy.workspace_change_weights`` (dataclass + validator)
  - ``GeneralConfig.agent_workspace_change_weights`` (Pydantic model + validator)
  - ``timeout_defaults.DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS`` (the
    documented conservative default)

All three layers must agree on the binary-only semantics (0.0 / 1.0
only) and the 5 canonical kind names (``source``/``log``/``cache``/
``artifact``/``other``).
"""

from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._options import (
    _policy_from_options,
    build_invoke_options_from_config,
)
from ralph.config.general_config import GeneralConfig
from ralph.timeout_defaults import DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS

# ---------------------------------------------------------------------------
# (a) DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS
# ---------------------------------------------------------------------------


def test_default_weights_match_documented_policy() -> None:
    """``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS`` matches the conservative
    policy documented in the plan: only ``source`` is 1.0, all others 0.0."""
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS == {
        "source": 1.0,
        "log": 0.0,
        "cache": 0.0,
        "artifact": 0.0,
        "other": 0.0,
    }


def test_default_weights_all_binary() -> None:
    """Every value in the defaults dict is in {0.0, 1.0}."""
    for value in DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS.values():
        assert value in {0.0, 1.0}


# ---------------------------------------------------------------------------
# (b) TimeoutPolicy.workspace_change_weights
# ---------------------------------------------------------------------------


def test_timeout_policy_default_weights_match_documented_policy() -> None:
    """A TimeoutPolicy with no explicit ``workspace_change_weights`` field
    falls back to the documented conservative default."""
    policy = TimeoutPolicy(idle_timeout_seconds=0.1)
    assert policy.workspace_change_weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_timeout_policy_rejects_unknown_key() -> None:
    """An unknown key in ``workspace_change_weights`` raises ValueError."""
    with pytest.raises(ValueError, match="not_a_kind"):
        TimeoutPolicy(
            idle_timeout_seconds=0.1,
            workspace_change_weights={"not_a_kind": 1.0},
        )


def test_timeout_policy_rejects_intermediate_weight() -> None:
    """An intermediate weight (0.5) raises ValueError; only {0.0, 1.0} allowed."""
    with pytest.raises(ValueError, match="binary"):
        TimeoutPolicy(
            idle_timeout_seconds=0.1,
            workspace_change_weights={"source": 0.5},
        )


def test_timeout_policy_rejects_weight_above_one() -> None:
    """A weight above 1.0 raises ValueError."""
    with pytest.raises(ValueError, match="binary"):
        TimeoutPolicy(
            idle_timeout_seconds=0.1,
            workspace_change_weights={"source": 2.0},
        )


def test_timeout_policy_accepts_partial_overrides() -> None:
    """A partial dict is accepted; the policy carries the operator's override
    verbatim (not the default for unspecified keys). The caller is responsible
    for normalizing via ``_normalize_workspace_change_weights`` at the seam
    between GeneralConfig and InvokeOptions."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        workspace_change_weights={"source": 1.0, "log": 1.0},
    )
    assert policy.workspace_change_weights == {"source": 1.0, "log": 1.0}


# ---------------------------------------------------------------------------
# (c) GeneralConfig.agent_workspace_change_weights
# ---------------------------------------------------------------------------


def test_general_config_default_weights() -> None:
    """``GeneralConfig.agent_workspace_change_weights`` defaults to the
    conservative policy."""
    config = GeneralConfig()
    assert config.agent_workspace_change_weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_general_config_accepts_partial_override() -> None:
    """Operators can opt a kind in by setting ``agent_workspace_change_weights``
    on GeneralConfig; Pydantic accepts a partial dict without filling from
    defaults (the policy builder merges via
    ``_normalize_workspace_change_weights`` at a later seam)."""
    config = GeneralConfig(agent_workspace_change_weights={"source": 1.0, "log": 1.0})
    assert config.agent_workspace_change_weights == {"source": 1.0, "log": 1.0}


def test_general_config_rejects_unknown_key() -> None:
    """An unknown key raises ValueError on the Pydantic validator."""
    with pytest.raises(ValueError, match="not_a_kind"):
        GeneralConfig(agent_workspace_change_weights={"not_a_kind": 1.0})


def test_general_config_rejects_intermediate_weight() -> None:
    """An intermediate weight raises ValueError on the Pydantic validator."""
    with pytest.raises(ValueError, match="binary"):
        GeneralConfig(agent_workspace_change_weights={"source": 0.5})


def test_general_config_field_description_mentions_all_kinds() -> None:
    """The field description mentions all 5 kinds and the binary semantics
    so operators reading the schema understand the contract."""
    config = GeneralConfig()
    description = config.model_fields["agent_workspace_change_weights"].description
    assert description is not None
    for kind in ("source", "log", "cache", "artifact", "other"):
        assert kind in description, (
            f"field description must mention WorkspaceChangeKind {kind!r}, got: {description!r}"
        )

    assert "binary" in description.lower() or "0.0" in description
    assert "1.0" in description


# ---------------------------------------------------------------------------
# (d) Round-trip: GeneralConfig -> InvokeOptions -> TimeoutPolicy
# ---------------------------------------------------------------------------


def test_general_config_to_invoke_options_threads_field() -> None:
    """A full round-trip: GeneralConfig -> InvokeOptions -> TimeoutPolicy
    preserves the operator's per-kind override end-to-end.

    This is the AC-02 / AC-08 contract: the new field is plumbed through
    every layer so a TOML change in ralph-workflow.toml actually affects
    the watchdog's per-kind workspace channel behavior.
    """
    config = GeneralConfig(
        agent_workspace_change_weights={"source": 1.0, "log": 1.0},
    )
    opts = build_invoke_options_from_config(config)
    policy = _policy_from_options(opts)
    # The operator's partial override flows into the policy verbatim.
    # (Full normalization happens via _normalize_workspace_change_weights
    # at a different seam.)
    assert policy.workspace_change_weights == {"source": 1.0, "log": 1.0}
