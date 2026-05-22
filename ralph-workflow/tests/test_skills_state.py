"""Tests for ralph.skills._state."""

from ralph.skills._state import CapabilityEntry, CapabilityState, CapabilityStatus


def test_capability_status_six_values() -> None:
    assert len(CapabilityStatus) == 6
    assert CapabilityStatus.NOT_INSTALLED == "not_installed"
    assert CapabilityStatus.INSTALLED_HEALTHY == "installed_healthy"
    assert CapabilityStatus.CONFIGURED_UNREACHABLE == "configured_unreachable"
    assert CapabilityStatus.INSTALLED_OUTDATED == "installed_outdated"
    assert CapabilityStatus.INSTALLED_DEGRADED == "installed_degraded"
    assert CapabilityStatus.NEEDS_REPAIR == "needs_repair"


def test_capability_entry_defaults() -> None:
    entry = CapabilityEntry()
    assert entry.status == CapabilityStatus.NOT_INSTALLED
    assert entry.last_check_ok_iso == ""
    assert entry.last_check_fail_iso == ""
    assert entry.update_available is False
    assert entry.ralph_version == ""


def test_capability_entry_full_init() -> None:
    entry = CapabilityEntry(
        status=CapabilityStatus.INSTALLED_HEALTHY,
        last_check_ok_iso="2025-01-01T00:00:00+00:00",
        last_check_fail_iso="",
        update_available=False,
        ralph_version="1.0.0",
    )
    assert entry.status == CapabilityStatus.INSTALLED_HEALTHY
    assert entry.ralph_version == "1.0.0"
    assert entry.update_available is False


def test_capability_entry_supports_model_copy() -> None:
    entry = CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY)
    # frozen=True prevents direct modification; use model_copy to create modified copy
    copy = entry.model_copy(update={"status": CapabilityStatus.NEEDS_REPAIR})
    assert copy.status == CapabilityStatus.NEEDS_REPAIR
    assert entry.status == CapabilityStatus.INSTALLED_HEALTHY  # original unchanged


def test_capability_state_has_four_named_fields() -> None:
    state = CapabilityState()
    # All four dependency-backed helpers are present
    assert hasattr(state, "web_search")
    assert hasattr(state, "visit_url")
    assert hasattr(state, "docs_mcp")
    assert hasattr(state, "skills")
    # All are CapabilityEntry instances
    assert isinstance(state.web_search, CapabilityEntry)
    assert isinstance(state.visit_url, CapabilityEntry)
    assert isinstance(state.docs_mcp, CapabilityEntry)
    assert isinstance(state.skills, CapabilityEntry)


def test_capability_state_supports_model_copy() -> None:
    state = CapabilityState()
    # frozen=True prevents direct modification; use model_copy to create modified copy
    copy = state.model_copy(
        update={"web_search": CapabilityEntry(status=CapabilityStatus.INSTALLED_OUTDATED)}
    )
    assert copy.web_search.status == CapabilityStatus.INSTALLED_OUTDATED
    assert state.web_search.status == CapabilityStatus.NOT_INSTALLED  # original unchanged


def test_capability_state_can_be_copied_with_update() -> None:
    state = CapabilityState()
    updated = state.model_copy(
        update={
            "web_search": CapabilityEntry(
                status=CapabilityStatus.INSTALLED_HEALTHY,
                ralph_version="2.0.0",
            ),
        }
    )
    assert updated.web_search.status == CapabilityStatus.INSTALLED_HEALTHY
    assert updated.web_search.ralph_version == "2.0.0"
    # Original unchanged
    assert state.web_search.status == CapabilityStatus.NOT_INSTALLED
