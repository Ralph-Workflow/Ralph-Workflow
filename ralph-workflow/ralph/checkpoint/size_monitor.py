"""Checkpoint size monitoring helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.checkpoint.size_alert import SizeAlert
from ralph.checkpoint.size_check_result import SizeCheckResult
from ralph.checkpoint.size_thresholds import SizeThresholds


@dataclass(frozen=True)
class CheckpointSizeMonitor:
    """Check serialized checkpoint sizes against configured thresholds."""

    thresholds: SizeThresholds = field(default_factory=SizeThresholds)

    @classmethod
    def new(cls) -> CheckpointSizeMonitor:
        """Create a monitor with default thresholds."""
        return cls()

    @classmethod
    def with_thresholds(cls, thresholds: SizeThresholds) -> CheckpointSizeMonitor:
        """Create a monitor with custom thresholds."""
        return cls(thresholds=thresholds)

    def check_size(self, size_bytes: int) -> SizeAlert | SizeCheckResult:
        """Return the alert level for a serialized checkpoint size."""
        if size_bytes >= self.thresholds.error_threshold:
            return SizeCheckResult(
                level=SizeAlert.ERROR.value,
                message=(
                    f"Checkpoint size {size_bytes} bytes exceeds hard limit "
                    f"{self.thresholds.error_threshold} bytes"
                ),
            )
        if size_bytes >= self.thresholds.warn_threshold:
            return SizeCheckResult(
                level=SizeAlert.WARNING.value,
                message=(
                    f"Checkpoint size {size_bytes} bytes exceeds warning threshold "
                    f"{self.thresholds.warn_threshold} bytes"
                ),
            )
        return SizeAlert.OK

    def check_json(self, json_text: str) -> SizeAlert | SizeCheckResult:
        """Check a serialized JSON payload by its byte length."""
        return self.check_size(len(json_text))
