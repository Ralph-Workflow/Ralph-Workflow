"""Tests for ralph.skills._recheck_policy RecheckPolicy configuration."""

from ralph.skills._recheck_policy import DEFAULT_POLICY, RecheckPolicy


class TestRecheckPolicy:
    def test_default_policy_values(self) -> None:
        assert DEFAULT_POLICY.healthy_recheck_hours == 24.0
        assert DEFAULT_POLICY.failed_recheck_hours == 1.0
        assert DEFAULT_POLICY.always_recheck_if_not_installed is True

    def test_custom_policy_creation(self) -> None:
        policy = RecheckPolicy(healthy_recheck_hours=48.0, failed_recheck_hours=0.5)
        assert policy.healthy_recheck_hours == 48.0
        assert policy.failed_recheck_hours == 0.5
