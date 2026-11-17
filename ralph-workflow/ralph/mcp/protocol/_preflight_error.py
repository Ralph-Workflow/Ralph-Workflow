from __future__ import annotations


class PreflightError(Exception):
    pass


class PermanentPreflightError(PreflightError):
    pass


class RetryablePreflightError(PreflightError):
    pass
