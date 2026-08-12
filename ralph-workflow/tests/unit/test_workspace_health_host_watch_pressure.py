"""Host watch-pressure signal tests (S-4).

Exercises the real ``read_host_pressure`` reader on the current host AND
the constrained-watch scenario via monkeypatch, then asserts the
``host_watch_pressure`` and ``active_maintenance`` fields on
``collect_workspace_health``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.diagnostics.host_pressure import read_host_pressure
from ralph.diagnostics.workspace_health import collect_workspace_health

if TYPE_CHECKING:
    import pytest


def test_read_host_pressure_returns_well_formed_payload() -> None:
    """The real reader returns a dict with the required keys (no stub)."""
    payload = read_host_pressure()
    assert isinstance(payload, dict)
    assert payload["attribution"] in {"certain", "uncertain"}
    assert isinstance(payload["signal"], str) and payload["signal"]
    value = payload["value"]
    assert value is None or (isinstance(value, float) and 0.0 <= value <= 1.0)
    assert isinstance(payload["safe_next_action"], str) and payload["safe_next_action"]


def test_host_watch_pressure_field_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``host_watch_pressure`` carries distinct ralph_descriptor and host_pressure."""
    from ralph.diagnostics import workspace_health as wh_module
    from ralph.workspace.awareness import (
        awareness_for_workspace,
        release_workspace_awareness,
    )

    stub_pressure = {
        "attribution": "uncertain",
        "signal": "inotify_max_user_watches",
        "value": 0.92,
        "safe_next_action": "Reduce workspace watch breadth.",
    }
    monkeypatch.setattr(wh_module, "read_host_pressure", lambda: dict(stub_pressure))

    # Register one active WorkspaceMonitor lease (constrained-watch exercise).
    from ralph.agents.invoke._workspace import WorkspaceMonitor

    monitor = WorkspaceMonitor(tmp_path)
    try:
        awareness_for_workspace(tmp_path).set_live_fallback("watch_capacity")
        payload = collect_workspace_health(tmp_path)
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    hwp = payload["host_watch_pressure"]
    assert isinstance(hwp, dict)
    assert set(hwp.keys()) == {"ralph_descriptor", "host_pressure"}
    ralph_descriptor = hwp["ralph_descriptor"]
    host_pressure = hwp["host_pressure"]
    # The two sub-objects are distinct dicts.
    assert ralph_descriptor is not host_pressure
    # ralph_descriptor is always certain regardless of host attribution.
    assert ralph_descriptor["attribution"] == "certain"
    assert ralph_descriptor["mode"] == "live_fallback"
    assert ralph_descriptor["owner"] == "WorkspaceMonitor"
    # host_pressure carries the stubbed uncertain value.
    assert host_pressure["attribution"] == "uncertain"
    assert host_pressure["signal"] == "inotify_max_user_watches"
    assert host_pressure["value"] == 0.92
    assert host_pressure["safe_next_action"] == "Reduce workspace watch breadth."


def test_active_maintenance_reports_retention_and_dirty_state(tmp_path: Path) -> None:
    """``active_maintenance`` carries retention_passes and dirty_scheduler_pending."""
    payload = collect_workspace_health(tmp_path)
    active = payload["active_maintenance"]
    assert isinstance(active, dict)
    assert isinstance(active["retention_passes"], int)
    assert isinstance(active["dirty_scheduler_pending"], bool)

    # A RetentionPassCoordinator that runs one sweep increments the
    # process-local pass counter; the health surface reflects it.
    from ralph.workspace.agent_dir_retention import (
        RetentionPassCoordinator,
        sweep_agent_dir,
    )

    coordinator = RetentionPassCoordinator()
    before = coordinator.passes
    sweep_agent_dir(tmp_path, keep_run_id=None, coordinator=coordinator)
    assert coordinator.passes == before + 1
