"""Black-box tests for TimeoutPolicy validation of the NO_PROGRESS_QUIET floor.

The dumb-kill floor (``no_progress_quiet_minimum_invocation_seconds``)
prevents the watchdog from firing NO_PROGRESS_QUIET within the first N
seconds of an agent run, so a recently-launched agent that is doing
real thinking work (planning, exploration, dispatching subagents) but
has not yet produced first-party activity evidence is not killed.

The tests below exercise the validation contract enforced by
``TimeoutPolicy.__post_init__``:

  - default value matches ``NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS``
  - constructor rejects 0.0 and negative values when set
  - constructor accepts None as disabled
  - constructor rejects minimum > no_progress_quiet_seconds
  - constructor accepts minimum <= no_progress_quiet_seconds
"""

from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.timeout_defaults import NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS


def test_default_no_progress_quiet_minimum_invocation_seconds_matches_constant() -> None:
    """Default value matches the package-level constant (120.0s)."""
    policy = TimeoutPolicy(idle_timeout_seconds=300.0)
    assert policy.no_progress_quiet_minimum_invocation_seconds == (
        NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds == 120.0


def test_constructor_accepts_none_as_disabled() -> None:
    """None disables the dumb-kill floor (documented escape hatch)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_minimum_invocation_seconds=None,
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds is None


def test_constructor_rejects_zero_when_set() -> None:
    """0.0 is rejected; the floor cannot be silently disabled by a 0.0 typo."""
    with pytest.raises(ValueError, match="must be positive when set"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_minimum_invocation_seconds=0.0,
        )


def test_constructor_rejects_negative_when_set() -> None:
    """Negative values are rejected; the floor cannot be silently disabled."""
    with pytest.raises(ValueError, match="must be positive when set"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_minimum_invocation_seconds=-1.0,
        )


def test_constructor_accepts_positive_floor() -> None:
    """Any positive float is allowed as the floor."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_minimum_invocation_seconds=60.0,
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds == 60.0


def test_constructor_rejects_floor_above_ceiling() -> None:
    """Floor must be <= no_progress_quiet_seconds when both are set."""
    with pytest.raises(ValueError, match="must be <= no_progress_quiet_seconds"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=200.0,
        )


def test_constructor_accepts_floor_equal_to_ceiling() -> None:
    """Floor == ceiling is allowed (floor is a sub-window of the ceiling)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=120.0,
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds == 120.0
    assert policy.no_progress_quiet_seconds == 120.0


def test_constructor_accepts_floor_with_none_ceiling() -> None:
    """Floor can be set when the ceiling itself is None (ceiling disabled)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=60.0,
    )
    assert policy.no_progress_quiet_seconds is None
    assert policy.no_progress_quiet_minimum_invocation_seconds == 60.0
