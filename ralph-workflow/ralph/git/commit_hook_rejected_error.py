from __future__ import annotations

import re
from typing import Final

from ralph.failure_digest import bounded_failure_output
from ralph.git.git_operation_error import GitOperationError

__all__ = ["HOOK_OUTPUT_LIMIT_CHARS", "CommitHookRejectedError"]

HOOK_OUTPUT_LIMIT_CHARS: Final[int] = 8192
_HOOK_REJECTION_LINE: Final = re.compile(
    r"❌|✗|\b(?:block(?:ed)?|reject(?:ed|ion)?|denied|forbidden|banned|violations?|"
    r"fail(?:s|ed|ure)?|errors?|fatal)\b",
    re.IGNORECASE,
)


class CommitHookRejectedError(GitOperationError):
    def __init__(self, output: str) -> None:
        self.hook_output = (
            bounded_failure_output(output, _HOOK_REJECTION_LINE, limit=HOOK_OUTPUT_LIMIT_CHARS)
            or "(the hook printed nothing)"
        )
        super().__init__("create_commit", f"a commit hook rejected the commit:\n{self.hook_output}")
