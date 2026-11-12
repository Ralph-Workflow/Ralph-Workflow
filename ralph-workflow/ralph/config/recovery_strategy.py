from enum import StrEnum


class RecoveryStrategy(StrEnum):
    FAIL = "fail"
    AUTO = "auto"
    FORCE = "force"
