"""Tests for ralph.skills._recheck_policy needs_recheck function."""

from datetime import UTC, datetime, timedelta

from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._recheck_policy import RecheckPolicy, needs_recheck


def _make_entry(
    status: CapabilityStatus,
    last_check_ok_iso: str = "",
    last_check_fail_iso: str = "",
) -> CapabilityEntry:
    return CapabilityEntry(
        status=status,
        last_check_ok_iso=last_check_ok_iso,
        last_check_fail_iso=last_check_fail_iso,
    )


def _iso(hours_ago: float) -> str:
    return (datetime.now(tz=UTC) - timedelta(hours=hours_ago)).isoformat()


class TestNeedsRecheck:
    def test_not_installed_always_needs_recheck(self) -> None:
        entry = _make_entry(CapabilityStatus.NOT_INSTALLED)
        assert needs_recheck(entry) is True

    def test_not_installed_with_always_false_returns_flag_value(self) -> None:
        entry = _make_entry(CapabilityStatus.NOT_INSTALLED)
        policy = RecheckPolicy(always_recheck_if_not_installed=False)
        assert needs_recheck(entry, policy) is False
        policy_true = RecheckPolicy(always_recheck_if_not_installed=True)
        assert needs_recheck(entry, policy_true) is True

    def test_healthy_within_ttl_does_not_need_recheck(self) -> None:
        entry = _make_entry(
            CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso=_iso(12),  # 12 hours ago, TTL is 24
        )
        assert needs_recheck(entry) is False

    def test_healthy_ttl_expired_needs_recheck(self) -> None:
        entry = _make_entry(
            CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso=_iso(25),  # 25 hours ago, TTL is 24
        )
        assert needs_recheck(entry) is True

    def test_healthy_no_timestamp_needs_recheck(self) -> None:
        entry = _make_entry(CapabilityStatus.INSTALLED_HEALTHY)
        assert needs_recheck(entry) is True

    def test_outdated_within_ttl_still_needs_recheck(self) -> None:
        entry = _make_entry(
            CapabilityStatus.INSTALLED_OUTDATED,
            last_check_ok_iso=_iso(12),
        )
        assert needs_recheck(entry) is False

    def test_needs_repair_after_failed_hours_expired(self) -> None:
        entry = _make_entry(
            CapabilityStatus.NEEDS_REPAIR,
            last_check_fail_iso=_iso(2),  # 2 hours ago, TTL is 1
        )
        assert needs_recheck(entry) is True

    def test_needs_repair_within_failed_ttl_does_not_need_recheck(self) -> None:
        entry = _make_entry(
            CapabilityStatus.NEEDS_REPAIR,
            last_check_fail_iso=_iso(0.5),  # 30 min ago, TTL is 1h
        )
        assert needs_recheck(entry) is False

    def test_configured_unreachable_after_fail_ttl_expired(self) -> None:
        entry = _make_entry(
            CapabilityStatus.CONFIGURED_UNREACHABLE,
            last_check_fail_iso=_iso(2),  # 2 hours ago, TTL is 1h
        )
        assert needs_recheck(entry) is True

    def test_invalid_iso_string_returns_none_and_triggers_recheck(self) -> None:
        entry = CapabilityEntry(
            status=CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso="not-a-valid-iso",
        )
        assert needs_recheck(entry) is True

    def test_custom_policy_respected(self) -> None:
        entry = _make_entry(
            CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso=_iso(2),  # 2 hours ago
        )
        policy = RecheckPolicy(healthy_recheck_hours=1.0)
        assert needs_recheck(entry, policy) is True
