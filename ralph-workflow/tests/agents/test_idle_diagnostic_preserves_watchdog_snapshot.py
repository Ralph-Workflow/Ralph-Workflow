from __future__ import annotations

import json
from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import AgentInactivityTimeoutError, InactivityTimeoutOpts
from ralph.agents.invoke.idle_diagnostic import as_idle_diagnostic
from ralph.pipeline.watchdog_fire_record import (
    WATCHDOG_FIRE_EVENTS_RELPATH,
    record_watchdog_fire,
    watchdog_fire_detail,
)

_SNAPSHOT = {
    "idle_elapsed_seconds": 300.6,
    "last_activity_kind": "tool_use",
    "last_alive_by": "os_descendant_busy",
    "cumulative_waiting_on_child_seconds": 0.0,
}


def test_conversion_keeps_the_nested_watchdog_snapshot() -> None:
    converted = as_idle_diagnostic(
        {"watchdog_snapshot": dict(_SNAPSHOT), "resume_safe": True, "opaque": object()}
    )
    assert converted == {"watchdog_snapshot": _SNAPSHOT, "resume_safe": True}


def test_watchdog_fire_record_carries_the_snapshot_that_explains_the_kill(tmp_path: Path) -> None:
    error = AgentInactivityTimeoutError(
        "agy",
        300.0,
        [],
        InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
            diagnostic=as_idle_diagnostic({"watchdog_snapshot": dict(_SNAPSHOT)}),
        ),
    )
    record_watchdog_fire(
        tmp_path, error, agent="agy/gemini-4-argon-low", phase="policy_remediation", recorded_at=1.0
    )
    record = json.loads((tmp_path / WATCHDOG_FIRE_EVENTS_RELPATH).read_text().splitlines()[0])
    assert record["snapshot"]["idle_elapsed_seconds"] == 300.6
    assert record["snapshot"]["last_alive_by"] == "os_descendant_busy"
    detail = watchdog_fire_detail(error)
    assert detail is not None
    assert "idle_elapsed_seconds=300.6" in detail
