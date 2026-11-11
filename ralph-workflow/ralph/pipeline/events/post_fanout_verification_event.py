
from dataclasses import dataclass


@dataclass(frozen=True)
class PostFanoutVerificationEvent:

    success: bool
    exit_code: int | None = None
    error: str | None = None
