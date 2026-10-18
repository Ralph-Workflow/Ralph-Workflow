"""B-signal: bounded CURRENT host-load check."""

import threading

from ralph.mcp.server._mcp_health_event import (
    CONTENDED_WATCH_RATIO,
    current_host_is_contended,
)


def test_current_host_is_contended_true_at_ratio() -> None:
    # 85% utilization of a 1000-watch budget = 850 watches live.
    target = int(1000 * CONTENDED_WATCH_RATIO)
    assert (
        current_host_is_contended(
            watch_limit_reader=lambda: 1000,
            watch_user_total_reader=lambda: target,
        )
        is True
    )


def test_current_host_is_contended_false_below_ratio() -> None:
    assert (
        current_host_is_contended(
            watch_limit_reader=lambda: 1000,
            watch_user_total_reader=lambda: 500,
        )
        is False
    )


def test_current_host_is_contended_false_when_kernel_data_unavailable() -> None:
    assert (
        current_host_is_contended(
            watch_limit_reader=lambda: None,
            watch_user_total_reader=lambda: None,
        )
        is False
    )


def test_current_host_is_contended_bounded_under_blocked_reader() -> None:
    hang_event = threading.Event()

    def blocked_reader() -> int | None:
        hang_event.wait(timeout=1.0)
        return 1000

    assert (
        current_host_is_contended(
            budget_seconds=0.05,
            watch_limit_reader=blocked_reader,
            watch_user_total_reader=lambda: 900,
        )
        is False
    )
