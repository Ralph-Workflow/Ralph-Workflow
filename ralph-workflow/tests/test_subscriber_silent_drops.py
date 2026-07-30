"""Tests for silent drop behavior in PipelineSubscriber - no DEBUG logging."""

from __future__ import annotations

import logging
import queue
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.display.subscriber import PipelineSubscriber

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.snapshot import PipelineSnapshot

# ``test_subscriber_silent_drops.py`` exercises PipelineSubscriber
# ``notify()`` with a tight queue and asserts no ``loguru`` DEBUG
# messages are emitted. The caplog bridge to loguru + the
# MagicMock-heavy state setup is intermittently slow under parallel
# xdist CPU contention and exceeds the 1s default test timeout.
# 5s is the documented minimum for non-trivial tests (see
# ``ralph/verify_timeout.py``) and is well under the 60s combined
# ``make verify`` budget. The 1s default policy is preserved
# globally; this module-level marker only relaxes the cap for the
# log-capture tests in this file.
pytestmark = pytest.mark.timeout_seconds(5)


@pytest.fixture
def subscriber(tmp_path: Path) -> PipelineSubscriber:
    """Create a PipelineSubscriber with a tiny queue for testing backpressure."""
    q: queue.Queue[PipelineSnapshot] = queue.Queue(maxsize=2)
    return PipelineSubscriber(
        queue=q,
        workspace_root=tmp_path,
        run_id="test-run",
    )


def test_dropped_count_increments_on_queue_full(subscriber: PipelineSubscriber) -> None:
    """PipelineSubscriber should increment dropped_count when queue is full."""
    state = MagicMock()
    state.phase = "planning"
    state.budget_caps = {"iteration": 1}
    state.outer_progress = {"iteration": 1}
    state.review_outcome = None
    state.interrupted_by_user = False
    state.last_error = None
    state.pr_url = None
    state.push_count = 0
    state.metrics.total_agent_calls = 0
    state.metrics.total_continuations = 0
    state.metrics.total_fallbacks = 0
    state.metrics.total_retries = 0
    state.worker_states = {}
    state.work_units = []
    state.previous_phase = None
    state.current_agent = MagicMock(return_value=None)

    # Fill the queue to capacity
    for _ in range(2):
        subscriber.notify(state)

    initial_drops = subscriber.dropped_count

    # Keep notifying to trigger drops
    for _ in range(5):
        subscriber.notify(state)

    assert subscriber.dropped_count > initial_drops


def test_no_loguru_debug_on_drop(
    subscriber: PipelineSubscriber,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PipelineSubscriber should NOT emit DEBUG log on dropped snapshots.

    Drop counts are surfaced only through completion_summary, not through
    per-drop log lines.
    """
    # Set up to capture loguru records via stdlib logging (loguru→stdlib bridge)
    # Loguru uses the 'loguru' logger by default
    logger_name = "loguru"

    state = MagicMock()
    state.phase = "planning"
    state.budget_caps = {"iteration": 1}
    state.outer_progress = {"iteration": 1}
    state.review_outcome = None
    state.interrupted_by_user = False
    state.last_error = None
    state.pr_url = None
    state.push_count = 0
    state.metrics.total_agent_calls = 0
    state.metrics.total_continuations = 0
    state.metrics.total_fallbacks = 0
    state.metrics.total_retries = 0
    state.worker_states = {}
    state.work_units = []
    state.previous_phase = None
    state.current_agent = MagicMock(return_value=None)

    # Fill the queue
    for _ in range(2):
        subscriber.notify(state)

    # Capture any DEBUG level records
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        # Trigger drops
        for _ in range(10):
            subscriber.notify(state)

    # Assert no "queue full" or "snapshot dropped" message was logged
    for record in caplog.records:
        assert "queue full" not in record.message.lower(), (
            f"Unexpected 'queue full' log found: {record.message}"
        )
        assert "snapshot dropped" not in record.message.lower(), (
            f"Unexpected 'snapshot dropped' log found: {record.message}"
        )


def test_dropped_count_accessible(subscriber: PipelineSubscriber) -> None:
    """dropped_count property should be accessible and return an int."""
    assert isinstance(subscriber.dropped_count, int)
    assert subscriber.dropped_count >= 0
