"""Result value returned by the bootstrap helpers.

Split from :mod:`ralph.config.bootstrap` so the bootstrap module stays under
the repository's 1000-line file cap enforced by
:mod:`ralph.testing.audit_repo_structure`. The public import path
``ralph.config.bootstrap.BootstrapResult`` is unchanged: the bootstrap module
re-exports this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["BootstrapResult"]


@dataclass(frozen=True)
class BootstrapResult:
    """Result of a bootstrap operation.

    Attributes:
        path: Target file path that was acted on.
        action: What happened: created, skipped, or regenerated.
        backup: Path to the .bak file if the original was backed up, else None.
    """

    path: Path
    action: Literal["created", "skipped", "regenerated"]
    backup: Path | None = None
