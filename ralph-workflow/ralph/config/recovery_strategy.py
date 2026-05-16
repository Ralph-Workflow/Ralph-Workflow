"""Recovery strategy enum for pipeline failures."""

from enum import StrEnum


class RecoveryStrategy(StrEnum):
    """Recovery strategy when pipeline encounters failures.

    Attributes:
        FAIL: Fail immediately on errors
        AUTO: Attempt automatic recovery
        FORCE: Force through errors
    """

    FAIL = "fail"
    AUTO = "auto"
    FORCE = "force"
