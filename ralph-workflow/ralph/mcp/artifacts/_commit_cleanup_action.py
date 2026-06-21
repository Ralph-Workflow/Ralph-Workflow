"""CommitCleanupAction artifact model for commit hardening."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ralph.pydantic_compat import RalphBaseModel

# Printable ASCII (no whitespace, no control chars, no non-ASCII). The
# ``+`` (one-or-more) qualifier also rejects empty/whitespace-only values
# because every accepted character is a non-whitespace printable ASCII
# codepoint.
_PRINTABLE_ASCII_PATTERN = r"^[\x21-\x7e]+$"


class CommitCleanupAction(RalphBaseModel):
    """A single cleanup action to perform on the repository."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["delete_file", "add_to_gitignore", "add_to_git_exclude"]
    path: str | None = None
    pattern: str | None = None

    @field_validator("path", "pattern")
    @classmethod
    def _reject_non_printable(cls, value: str | None) -> str | None:
        """Reject empty, whitespace-only, control-char, or non-ASCII values.

        Both ``.gitignore`` and ``.git/info/exclude`` treat lines starting
        with ``#`` as comments and treat whitespace-prefixed lines the same
        way. A malformed artifact that smuggles a newline or ``#`` into a
        path/pattern could silently disable a real exclude rule. The
        regex enforces printable ASCII at the Pydantic layer so the
        artifact schema is the single source of truth.
        """
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
        """Reject ``#``-prefixed values to prevent silent comment-line injection.

        Both ``.gitignore`` and ``.git/info/exclude`` treat lines starting
        with ``#`` as comments. A malformed artifact that ships a path
        or pattern starting with ``#`` could silently disable a real
        exclude rule. The validator rejects ``#`` as the first character
        at Pydantic validation time.
        """
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
