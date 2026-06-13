"""Architecture invariants for subagent visibility and waiting-state projection."""

from __future__ import annotations

import inspect

from ralph import supervising
from ralph.display import parallel_display
from ralph.pipeline import effect_executor


def _module_source(module: object) -> str:
    return inspect.getsource(module)


def test_invoke_start_uses_unit_scoped_display_path() -> None:
    """Invoke-start activity must go through the unit-scoped display path."""
    content = _module_source(effect_executor)
    legacy_fallback = (
        "emit_activity_line(\n"
        "            display,\n"
        "            None,\n"
        "            invoke_line,"
    )
    assert 'display.emit(effect.agent_name, invoke_line)' in content
    assert legacy_fallback in content


def test_parallel_display_emit_records_subscriber_activity_for_unit_scoped_lines() -> None:
    """Unit-scoped raw emits must update subscriber state before console emission."""
    content = _module_source(parallel_display)
    assert 'self._subscriber.record_activity(' in content
    assert 'unit_id=unit_id,' in content
    assert 'agent_name=unit_id,' in content


def test_supervising_recent_activity_appends_waiting_without_hiding_output() -> None:
    """Supervisor projection must keep last activity and append waiting after it."""
    content = _module_source(supervising)
    marker = "def _recent_activity(snapshot: PipelineSnapshot) -> tuple[str, ...]:"
    recent_activity = content.split(marker, 1)[1]
    last_idx = recent_activity.index('if snapshot.last_activity_line is not None:')
    waiting_idx = recent_activity.index('if snapshot.waiting_status_line is not None:')
    assert last_idx < waiting_idx
