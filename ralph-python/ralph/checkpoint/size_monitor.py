"""Checkpoint size monitoring helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class _StringEnum(StrEnum):
    """Compat base for string-valued enums across tooling versions."""


class SizeAlert(_StringEnum):
    """Alert level for checkpoint size checks."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class SizeCheckResult:
    """Structured checkpoint size check result."""

    level: str
    message: str | None = None


@dataclass(frozen=True)
class SizeThresholds:
    """Warning and error thresholds in bytes."""

    warn_threshold: int = 1_572_864
    error_threshold: int = 2_097_152


@dataclass(frozen=True)
class CheckpointSizeMonitor:
    """Check serialized checkpoint sizes against configured thresholds."""

    thresholds: SizeThresholds = SizeThresholds()

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
