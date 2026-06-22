"""Black-box tests for the new ``agent_no_progress_quiet_heartbeat_ceiling_seconds`` config field.

These tests cover three layers:
  - ``TimeoutPolicy.no_progress_quiet_heartbeat_ceiling_seconds`` (dataclass + validator)
  - ``GeneralConfig.agent_no_progress_quiet_heartbeat_ceiling_seconds`` (Pydantic model
    + cross-field validator)
  - ``timeout_defaults.NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS`` (the documented
    default)

All three layers must agree on the cross-field contract: when both
``agent_no_progress_quiet_heartbeat_ceiling_seconds`` and
``agent_no_progress_quiet_seconds`` are set, the heartbeat ceiling
must be <= the dumb-kill ceiling. The heartbeat-only branch is a
SHORTER, ORTHOGONAL ceiling that fires BEFORE the dumb-kill
``NO_PROGRESS_QUIET`` ceiling; if the heartbeat ceiling were longer
than the outer ceiling the heartbeat branch would never fire and the
operator's intent would be silently defeated.
"""

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

# ---------------------------------------------------------------------------
# (a) Default constant
# ---------------------------------------------------------------------------


def test_default_heartbeat_ceiling_le_default_no_progress_quiet_constant() -> None:
    """``NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS`` is <= ``NO_PROGRESS_QUIET_SECONDS``.

    The cross-field validator requires ``heartbeat_ceiling <=
    no_progress_quiet_seconds`` whenever both are set. The default
    constants MUST satisfy the constraint so a default-constructed
    ``GeneralConfig`` and ``TimeoutPolicy`` are valid out of the box.
    Without this invariant the operator cannot rely on the documented
    defaults.
    """
    assert NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS is not None
    assert NO_PROGRESS_QUIET_SECONDS is not None
    assert NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS <= NO_PROGRESS_QUIET_SECONDS, (
        f"default NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS"
        f" ({NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS}) MUST be <="
        f" default NO_PROGRESS_QUIET_SECONDS ({NO_PROGRESS_QUIET_SECONDS})"
        f" so the cross-field validator accepts a default constructor"
    )


# ---------------------------------------------------------------------------
# (b) GeneralConfig.agent_no_progress_quiet_heartbeat_ceiling_seconds
# ---------------------------------------------------------------------------


def test_general_config_default_heartbeat_ceiling() -> None:
    """``GeneralConfig.agent_no_progress_quiet_heartbeat_ceiling_seconds`` defaults to
    ``NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS`` and satisfies the
    cross-field constraint.
    """
    config = GeneralConfig()
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == (
        NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS
    )


def test_general_config_rejects_heartbeat_ceiling_above_no_progress_quiet_seconds() -> None:
    """The cross-field ``model_validator`` rejects
    ``agent_no_progress_quiet_heartbeat_ceiling_seconds >
    agent_no_progress_quiet_seconds`` when both are set.

    Without this guard an operator could configure the heartbeat-only
    ceiling to be LONGER than the dumb-kill ``agent_no_progress_quiet_seconds``
    ceiling, which would silently defeat the heartbeat-only branch
    (the outer ceiling would trip first).
    """
    with pytest.raises(
        ValueError,
        match="agent_no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        GeneralConfig(
            agent_no_progress_quiet_seconds=120.0,
            agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )


def test_general_config_accepts_heartbeat_ceiling_equal_to_no_progress_quiet_seconds() -> None:
    """``agent_no_progress_quiet_heartbeat_ceiling_seconds ==
    agent_no_progress_quiet_seconds`` is allowed (the degenerate
    equal case).

    The heartbeat-only branch is a SHORTER, ORTHOGONAL ceiling.
    Equality is the degenerate case: the heartbeat branch and the
    dumb-kill branch trip at the same time. The contract permits
    this so an operator who wants the heartbeat-only branch to fire
    at the dumb-kill ceiling can do so without an off-by-one in the
    cross-field guard.
    """
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=240.0,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == 240.0
    assert config.agent_no_progress_quiet_seconds == 240.0


def test_general_config_accepts_heartbeat_ceiling_below_no_progress_quiet_seconds() -> None:
    """``agent_no_progress_quiet_heartbeat_ceiling_seconds <
    agent_no_progress_quiet_seconds`` is allowed (the happy path).
    """
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=240.0,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=180.0,
    )
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == 180.0
    assert config.agent_no_progress_quiet_seconds == 240.0


def test_general_config_accepts_heartbeat_ceiling_with_none_no_progress_quiet_seconds() -> None:
    """``agent_no_progress_quiet_heartbeat_ceiling_seconds`` is allowed
    when ``agent_no_progress_quiet_seconds`` is ``None``.

    The cross-field guard is only enforced when BOTH fields are set.
    A ``None`` ``agent_no_progress_quiet_seconds`` (dumb-kill
    disabled) does NOT block the heartbeat-only ceiling; the
    heartbeat branch operates independently of the dumb-kill
    floor/ceiling and is consulted first in
    ``_is_no_progress_quiet``.
    """
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=None,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert config.agent_no_progress_quiet_seconds is None
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds == 240.0


def test_general_config_accepts_heartbeat_ceiling_none() -> None:
    """``agent_no_progress_quiet_heartbeat_ceiling_seconds=None``
    disables the heartbeat-only ceiling (documented escape hatch).
    """
    config = GeneralConfig(
        agent_no_progress_quiet_heartbeat_ceiling_seconds=None,
    )
    assert config.agent_no_progress_quiet_heartbeat_ceiling_seconds is None


def test_general_config_field_description_mentions_constraint() -> None:
    """The field description mentions the <= ``agent_no_progress_quiet_seconds``
    cross-field constraint so operators reading the schema understand
    the contract.
    """
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


# ---------------------------------------------------------------------------
# (c) Round-trip: GeneralConfig -> TimeoutPolicy
# ---------------------------------------------------------------------------
# (build_invoke_options_from_config + _policy_from_options)


def test_general_config_heartbeat_ceiling_threads_through_to_timeout_policy() -> None:
    """An operator's ``agent_no_progress_quiet_heartbeat_ceiling_seconds``
    override on ``GeneralConfig`` flows into the ``TimeoutPolicy`` so
    the watchdog actually consumes the operator's value.

    This is the AC-08 contract: the new field is plumbed through
    every layer so a TOML change in ralph-workflow.toml actually
    affects the watchdog's heartbeat-only ceiling behavior.
    """
    config = GeneralConfig(
        agent_no_progress_quiet_seconds=240.0,
        agent_no_progress_quiet_heartbeat_ceiling_seconds=180.0,
    )
    opts = build_invoke_options_from_config(config)
    policy = _policy_from_options(opts)
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds == 180.0
    assert policy.no_progress_quiet_seconds == 240.0


def test_general_config_rejects_invalid_combination_round_trip() -> None:
    """A TOML change that violates the cross-field constraint is
    rejected at the ``GeneralConfig`` boundary so it never reaches
    the watchdog.

    Without the cross-field ``model_validator`` the operator's
    invalid combination would be silently accepted by Pydantic and
    then caught by ``TimeoutPolicy.__post_init__`` at the policy
    construction seam. Pinning the rejection at the
    ``GeneralConfig`` boundary ensures the failure mode is
    deterministic and surfaces in the config loader rather than in
    a later seam.
    """
    with pytest.raises(
        ValueError,
        match="agent_no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        GeneralConfig(
            agent_no_progress_quiet_seconds=120.0,
            agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )


# ---------------------------------------------------------------------------
# (d) TimeoutPolicy validator mirrors the GeneralConfig validator
# ---------------------------------------------------------------------------


def test_timeout_policy_validator_mirrors_general_config_validator() -> None:
    """The ``TimeoutPolicy`` and ``GeneralConfig`` validators MUST
    agree on the same cross-field constraint.

    If the operators are consistent at the ``GeneralConfig`` boundary
    (TOML -> Pydantic) and the ``TimeoutPolicy.__post_init__``
    validator mirrors the same cross-field check, the
    ``build_invoke_options_from_config`` + ``_policy_from_options``
    pipeline never constructs an invalid ``TimeoutPolicy``. Pinning
    this contract here catches a drift between the two validators
    in a single black-box test.
    """
    # 1. TimeoutPolicy: invalid combination is rejected.
    with pytest.raises(
        ValueError,
        match="no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )

    # 2. GeneralConfig: invalid combination is rejected.
    with pytest.raises(
        ValueError,
        match="agent_no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        GeneralConfig(
            agent_no_progress_quiet_seconds=120.0,
            agent_no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )
