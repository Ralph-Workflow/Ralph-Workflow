from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ralph.pydantic_compat import RalphBaseModel

_PRINTABLE_ASCII_PATTERN = r"^[\x21-\x7e]+$"


class CommitReviewAction(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["delete_file", "add_to_gitignore", "add_to_git_exclude"]
    path: str | None = None
    pattern: str | None = None

    @field_validator("path", "pattern")
    @classmethod
    def _reject_non_printable(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not re.match(_PRINTABLE_ASCII_PATTERN, value):
            raise ValueError(
                "path and pattern must be printable ASCII (no control "
                "characters, no whitespace-only values, no non-ASCII "
                "characters); leading '#' is also rejected -- see "
                "_reject_comment_prefix below."
            )
        return value

    @field_validator("path", "pattern")
    @classmethod
    def _reject_comment_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value.startswith("#"):
            raise ValueError(
                "path and pattern must not start with '#' (comment-line "
                "injection vector in .gitignore and .git/info/exclude)."
            )
        return value

    @model_validator(mode="after")
    def _validate_action_fields(self) -> Self:
        if self.action == "delete_file" and not self.path:
            raise ValueError("'path' is required for 'delete_file' action")
        elif self.action in ("add_to_gitignore", "add_to_git_exclude") and not self.pattern:
            raise ValueError(f"'pattern' is required for '{self.action}' action")
        return self
