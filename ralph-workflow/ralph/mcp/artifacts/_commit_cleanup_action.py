"""CommitCleanupAction artifact model for commit hardening."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ralph.pydantic_compat import RalphBaseModel


class CommitCleanupAction(RalphBaseModel):
    """A single cleanup action to perform on the repository."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["delete_file", "add_to_gitignore", "add_to_git_exclude"]
    path: str | None = None
    pattern: str | None = None

    @model_validator(mode="after")
    def _validate_action_fields(self) -> Self:
        if self.action == "delete_file" and not self.path:
            raise ValueError("'path' is required for 'delete_file' action")
        elif self.action in ("add_to_gitignore", "add_to_git_exclude") and not self.pattern:
            raise ValueError(f"'pattern' is required for '{self.action}' action")
        return self
