from enum import StrEnum


class CompletionQuiesceDecision(StrEnum):
    WAIT = "wait"
    TEARDOWN = "teardown"
    TEARDOWN_PARTIAL = "teardown_partial"
