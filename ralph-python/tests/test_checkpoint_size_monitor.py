"""Unit tests for checkpoint size monitoring."""

from __future__ import annotations

from importlib import import_module

checkpoint_module = import_module("ralph.checkpoint")
CheckpointSizeMonitor = checkpoint_module.CheckpointSizeMonitor
SizeAlert = checkpoint_module.SizeAlert
SizeThresholds = checkpoint_module.SizeThresholds


def test_checkpoint_size_monitor_reports_warning_and_error_thresholds() -> None:
    """Monitor should distinguish healthy, warning, and error sizes."""
    monitor = CheckpointSizeMonitor.with_thresholds(SizeThresholds(10, 20))

    assert monitor.check_size(5) == SizeAlert.OK
    assert monitor.check_size(15).level == "warning"
    assert monitor.check_size(25).level == "error"


def test_checkpoint_size_monitor_checks_json_length() -> None:
    """JSON checks should use the serialized payload length."""
    monitor = CheckpointSizeMonitor.with_thresholds(SizeThresholds(4, 8))

    assert monitor.check_json("12345").level == "warning"
