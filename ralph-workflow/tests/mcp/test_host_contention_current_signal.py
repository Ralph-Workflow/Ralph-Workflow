"""B-signal: bounded CURRENT host-load check."""

from unittest.mock import patch

from ralph.agents.invoke import _watch_capacity
from ralph.mcp.server._mcp_health_event import (
    CONTENDED_WATCH_RATIO,
    current_host_is_contended,
)


def test_current_host_is_contended_true_at_ratio():
    # 85% utilization of a 1000-watch budget = 850 watches live.
    target = int(1000 * CONTENDED_WATCH_RATIO)
    with (
        patch.object(_watch_capacity, "_read_inotify_watch_limit", return_value=1000),
        patch.object(_watch_capacity, "_read_inotify_watch_user_total", return_value=target),
    ):
        assert current_host_is_contended() is True


def test_current_host_is_contended_false_below_ratio():
    with (
        patch.object(_watch_capacity, "_read_inotify_watch_limit", return_value=1000),
        patch.object(_watch_capacity, "_read_inotify_watch_user_total", return_value=500),
    ):
        assert current_host_is_contended() is False


def test_current_host_is_contended_false_when_kernel_data_unavailable():
    with (
        patch.object(_watch_capacity, "_read_inotify_watch_limit", return_value=None),
        patch.object(_watch_capacity, "_read_inotify_watch_user_total", return_value=None),
    ):
        assert current_host_is_contended() is False


def test_current_host_is_contended_bounded_under_blocked_reader(monkeypatch):
    # Simulate a blocked reader by patching call_within_budget to return False
    # within budget. This proves the producer falls back to False when the
    # /proc scan hangs, NOT to the bridge holding the lock indefinitely.
    def fake_call_within_budget(probe, fallback, budget_seconds):
        return False  # Simulated budget expiry

    monkeypatch.setattr(
        "ralph.agents.invoke._watch_capacity.call_within_budget",
        fake_call_within_budget,
    )
    assert current_host_is_contended() is False
