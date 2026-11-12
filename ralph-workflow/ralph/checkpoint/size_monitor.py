from __future__ import annotations

from dataclasses import dataclass, field

from ralph.checkpoint.size_alert import SizeAlert
from ralph.checkpoint.size_check_result import SizeCheckResult
from ralph.checkpoint.size_thresholds import SizeThresholds


@dataclass(frozen=True)
class CheckpointSizeMonitor:
    thresholds: SizeThresholds = field(default_factory=SizeThresholds)

    @classmethod
    def new(cls) -> CheckpointSizeMonitor:
        return cls()

    @classmethod
    def with_thresholds(cls, thresholds: SizeThresholds) -> CheckpointSizeMonitor:
        return cls(thresholds=thresholds)

    def check_size(self, size_bytes: int) -> SizeAlert | SizeCheckResult:
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
        return self.check_size(len(json_text))
