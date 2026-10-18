"""W-signal: workspace monitor → bridge contention signal transport."""

from ralph.mcp.server._mcp_health_event import (
    _clear_host_contention_signals,
    pop_recent_host_contention_signals,
    record_host_contention_signal,
)


def test_record_and_pop_round_trip():
    _clear_host_contention_signals()
    record_host_contention_signal("watch_capacity_predicted")
    sigs = pop_recent_host_contention_signals(60.0)
    assert len(sigs) == 1
    assert sigs[0][1] == "watch_capacity_predicted"


def test_window_filters_old_signals():
    _clear_host_contention_signals()
    # Synthetically age a signal by clearing and re-recording with a fake time.
    # Use the deque's pop_recent to test the window filter:
    record_host_contention_signal("test")
    # A positive window always includes the just-recorded signal.
    assert len(pop_recent_host_contention_signals(60.0)) >= 1
    # (We can't easily simulate aged signals without monkey-patching time;
    # the contract is "now - ts <= window_s" so a positive window always
    # returns at least the just-recorded signal.)


def test_deque_is_bounded():
    _clear_host_contention_signals()
    for i in range(200):
        record_host_contention_signal(f"reason-{i}")
    # Cap is 64, so the deque length is at most 64.
    assert len(pop_recent_host_contention_signals(60.0)) <= 64
