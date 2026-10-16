"""R2 / R4 invariant tests for startup timeout constants and import-time guards.

These tests validate that:

1. The import-time invariants in ``_startup_timeouts`` are well-formed and
   hold at their documented defaults (no ``assert`` — these are ``if/raise``
   so they survive ``python -O``).
2. ``_PROBE_SAFETY_S`` is a positive float consumed by the runtime.
3. The computed ``_MAX_UPSTREAMS_AT_DEFAULTS`` is consistent with the
   default preflight budget.
4. An undersized preflight budget is caught at runtime
   (``mcp_upstream_probe_timeout_from_env`` raises ``RuntimeError`` when
   the ceiling would be at or below ``_PROBE_SAFETY_S``).

These tests deliberately avoid ``time.sleep`` and do not assume implementation
internals beyond the documented public API of
``ralph.mcp.protocol._startup_timeouts``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

# Import the public surface to verify _PROBE_SAFETY_S is exported.
from ralph.mcp.protocol._startup_timeouts import (
    _MAX_UPSTREAMS_AT_DEFAULTS,
    _PROBE_BUDGET_SHARE,
    _PROBE_SAFETY_S,
    mcp_preflight_timeout_from_env,
    mcp_upstream_probe_timeout_from_env,
    mcp_upstream_probe_timeout_seconds,
)


class TestImportTimeInvariants:
    """The module must pass its own import-time checks at documented defaults."""

    def test_probe_safety_is_positive(self) -> None:
        assert isinstance(_PROBE_SAFETY_S, float)
        assert _PROBE_SAFETY_S > 0

    def test_probe_budget_share_is_a_valid_fraction(self) -> None:
        assert isinstance(_PROBE_BUDGET_SHARE, float)
        assert 0 < _PROBE_BUDGET_SHARE < 1

    def test_max_upstreams_at_defaults_is_at_least_one(self) -> None:
        assert isinstance(_MAX_UPSTREAMS_AT_DEFAULTS, int)
        assert _MAX_UPSTREAMS_AT_DEFAULTS >= 1

    def test_default_preflight_budget_exceeds_probe_safety(self) -> None:
        """Import-time invariant: default preflight * share > _PROBE_SAFETY_S.

        The child's failure-reporting window after probes finish is
        preflight * (1 - share).  The ceiling (preflight * share) must be
        strictly above _PROBE_SAFETY_S so that even a worst-case probe that
        hits the ceiling still leaves the child enough time to surface the
        stall before the supervisor SIGKILLs it.
        """
        default_preflight = mcp_preflight_timeout_from_env({})
        ceiling = default_preflight * _PROBE_BUDGET_SHARE
        assert ceiling.total_seconds() > _PROBE_SAFETY_S, (
            f"default ceiling {ceiling.total_seconds():.1f}s must be > "
            f"_PROBE_SAFETY_S {_PROBE_SAFETY_S}s"
        )

    def test_max_upstreams_at_defaults_math_is_consistent(self) -> None:
        """_MAX_UPSTREAMS_AT_DEFAULTS is the largest N such that
        N * _PROBE_SAFETY_S < preflight * share.

        This guarantees N concurrent probes can each receive a budget at or
        above the safety margin without exhausting the child's failure-
        reporting window.
        """
        default_preflight = mcp_preflight_timeout_from_env({})
        ceiling = default_preflight * _PROBE_BUDGET_SHARE
        # Each of N upstreams gets ceiling/N budget; all N must be ≥ _PROBE_SAFETY_S
        # so the child can surface a stall before the supervisor kills it.
        # Re-arranged: N ≤ floor((ceiling - ε) / _PROBE_SAFETY_S) for any ε>0.
        # The integer formula used in the module is:
        #   _MAX_UPSTREAMS_AT_DEFAULTS = int((ceiling - _PROBE_SAFETY_S) / _PROBE_SAFETY_S)
        expected = int((ceiling.total_seconds() - _PROBE_SAFETY_S) / _PROBE_SAFETY_S)
        assert expected == _MAX_UPSTREAMS_AT_DEFAULTS, (
            f"_MAX_UPSTREAMS_AT_DEFAULTS {_MAX_UPSTREAMS_AT_DEFAULTS} "
            f"≠ expected {expected} from default ceiling {ceiling.total_seconds():.1f}s"
        )


class TestProbeTimeoutAtDefaults:
    """At defaults the per-upstream budget is large enough for all documented use-cases."""

    def test_default_preflight_is_30_seconds(self) -> None:
        assert mcp_preflight_timeout_from_env({}) == timedelta(seconds=30)

    def test_default_probe_ceiling_is_15_seconds(self) -> None:
        ceiling = mcp_upstream_probe_timeout_from_env({})
        assert ceiling == timedelta(seconds=15)

    def test_default_probe_ceiling_seconds(self) -> None:
        assert mcp_upstream_probe_timeout_seconds({}) == 15.0

    def test_probe_ceiling_strictly_below_preflight(self) -> None:
        preflight = mcp_preflight_timeout_from_env({})
        ceiling = mcp_upstream_probe_timeout_from_env({})
        assert ceiling < preflight
        # Remaining time after probes = preflight - ceiling = 15s, well above safety
        remaining = preflight - ceiling
        assert remaining.total_seconds() > _PROBE_SAFETY_S


class TestUndersizedPreflightAtRuntime:
    """An undersized preflight is caught at runtime when the ceiling ≤ _PROBE_SAFETY_S."""

    def test_preflight_below_minimum_raises_runtime_error(self) -> None:
        """When preflight * share ≤ _PROBE_SAFETY_S, the ceiling is too small
        for the child to report a stalled probe. The function must raise
        ``RuntimeError`` rather than silently returning a value that would
        cause a silent SIGKILL in production.

        Minimum preflight to pass: _PROBE_SAFETY_S / _PROBE_BUDGET_SHARE = 4000 ms.
        """
        # Just above the minimum: 4001ms → ceiling = 2000.5ms > 2000ms ✓
        env_just_above = {"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "4001"}
        ceiling_above = mcp_upstream_probe_timeout_from_env(env_just_above)
        assert ceiling_above.total_seconds() > _PROBE_SAFETY_S

        # At the minimum (4000ms): ceiling = 2000ms == _PROBE_SAFETY_S → must raise
        env_at_minimum = {"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "4000"}
        with pytest.raises(RuntimeError, match="preflight timeout.*probe ceiling.*_PROBE_SAFETY_S"):
            mcp_upstream_probe_timeout_from_env(env_at_minimum)

        # Below minimum: 1000ms → ceiling = 500ms < 2000ms → must raise
        env_too_small = {"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "1000"}
        with pytest.raises(RuntimeError, match="preflight timeout.*probe ceiling.*_PROBE_SAFETY_S"):
            mcp_upstream_probe_timeout_from_env(env_too_small)

    def test_explicit_probe_timeout_below_ceiling_is_returned(self) -> None:
        """An explicit RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS below the ceiling
        is returned as-is (the operator explicitly requested a shorter budget)."""
        # preflight=30s → ceiling=15s; explicit probe=1s < ceiling → returned
        env = {
            "RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "30000",
            "RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS": "1000",
        }
        result = mcp_upstream_probe_timeout_from_env(env)
        assert result == timedelta(seconds=1)

    def test_small_explicit_probe_also_raises(self) -> None:
        """If preflight itself is small, even an explicit probe timeout
        clamped to the ceiling still fails the safety floor."""
        env = {
            "RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "4000",  # ceiling = 2000ms == safety
            "RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS": "1000",
        }
        with pytest.raises(RuntimeError, match="preflight timeout.*probe ceiling.*_PROBE_SAFETY_S"):
            mcp_upstream_probe_timeout_from_env(env)


class TestProbeSafetyConstantExistence:
    """_PROBE_SAFETY_S must be a consumable constant for the runtime and tests."""

    def test_probe_safety_s_is_exported(self) -> None:
        # The brief requires _PROBE_SAFETY_S as a consumable constant
        from ralph.mcp.protocol._startup_timeouts import _PROBE_SAFETY_S
        assert _PROBE_SAFETY_S == 2.0

    def test_probe_safety_s_is_float(self) -> None:
        assert type(_PROBE_SAFETY_S) is float

    def test_probe_safety_s_is_consumed_in_max_upstreams_formula(self) -> None:
        """Verify _PROBE_SAFETY_S participates in _MAX_UPSTREAMS_AT_DEFAULTS."""
        default_preflight = mcp_preflight_timeout_from_env({})
        ceiling = default_preflight * _PROBE_BUDGET_SHARE
        # The formula is: int((ceiling - _PROBE_SAFETY_S) / _PROBE_SAFETY_S)
        computed = int((ceiling.total_seconds() - _PROBE_SAFETY_S) / _PROBE_SAFETY_S)
        assert computed == _MAX_UPSTREAMS_AT_DEFAULTS
