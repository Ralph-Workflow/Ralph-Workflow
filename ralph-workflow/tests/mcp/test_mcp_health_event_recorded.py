"""``record_stage`` must append available/unavailable decisions to ``_EVENTS``."""

from __future__ import annotations

import ralph.mcp.server._mcp_health_event as m
from ralph.mcp.server._mcp_health_event import record_stage


def test_record_stage_available_log() -> None:
    record_stage("test_available", available=True)
    assert ("test_available", True) in m._EVENTS


def test_record_stage_unavailable_log() -> None:
    record_stage("test_unavailable", available=False)
    assert ("test_unavailable", False) in m._EVENTS
