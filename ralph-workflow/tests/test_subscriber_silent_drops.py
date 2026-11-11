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

pytestmark = pytest.mark.timeout_seconds(5)


@pytest.fixture
def subscriber(tmp_path: Path) -> PipelineSubscriber:
    q: queue.Queue[PipelineSnapshot] = queue.Queue(maxsize=2)
    return PipelineSubscriber(
        queue=q,
        workspace_root=tmp_path,
        run_id="test-run",
    )


def test_dropped_count_increments_on_queue_full(subscriber: PipelineSubscriber) -> None:
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

    for _ in range(2):
        subscriber.notify(state)

    initial_drops = subscriber.dropped_count

    for _ in range(5):
        subscriber.notify(state)

    assert subscriber.dropped_count > initial_drops


def test_no_loguru_debug_on_drop(
    subscriber: PipelineSubscriber,
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    for _ in range(2):
        subscriber.notify(state)

    with caplog.at_level(logging.DEBUG, logger=logger_name):
        for _ in range(10):
            subscriber.notify(state)

    for record in caplog.records:
        assert "queue full" not in record.message.lower(), (
            f"Unexpected 'queue full' log found: {record.message}"
        )
        assert "snapshot dropped" not in record.message.lower(), (
            f"Unexpected 'snapshot dropped' log found: {record.message}"
        )


def test_dropped_count_accessible(subscriber: PipelineSubscriber) -> None:
    assert isinstance(subscriber.dropped_count, int)
    assert subscriber.dropped_count >= 0
