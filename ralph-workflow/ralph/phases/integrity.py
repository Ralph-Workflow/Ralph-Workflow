"""PROMPT.md integrity helpers for Python phase execution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pro_support.prompt import resolve_effective_prompt_path

DEFAULT_PROMPT_PATH = "PROMPT.md"
DEFAULT_BACKUP_PATHS: tuple[str, ...] = (
    ".agent/prompt.backup.md",
    ".agent/PROMPT.md.bak",
    ".agent/PROMPT.backup.md",
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ralph.workspace.protocol import Workspace


@dataclass(frozen=True)
class IntegrityResult:
    """Result of a PROMPT.md integrity verification pass."""

    ok: bool
    restored: bool
    prompt_path: str = DEFAULT_PROMPT_PATH
    backup_path: str | None = None
    message: str = ""


def default_prompt_path(
    workspace_root: Path,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the env-aware effective source-prompt path for a workspace.

    Thin convenience wrapper around
    :func:`ralph.pro_support.prompt.resolve_effective_prompt_path`.
    """
    return resolve_effective_prompt_path(workspace_root, env if env is not None else os.environ)


def verify_prompt_integrity(
    workspace: Workspace,
    *,
    prompt_path: str | None = None,
) -> IntegrityResult:
    """Check that PROMPT.md exists and is non-empty.

    When ``prompt_path`` is ``None`` the effective path is resolved
    through
    :func:`ralph.pro_support.prompt.resolve_effective_prompt_path`
    so the ``PROMPT_PATH`` env var is honoured in Pro mode. The
    legacy literal ``"PROMPT.md"`` is preserved when the caller passes
    that string explicitly so the existing tests can keep their
    explicit defaults.
    """
    if prompt_path is None:
        prompt_path = str(resolve_effective_prompt_path(workspace.absolute_path("."), os.environ))
    if not workspace.exists(prompt_path):
        return IntegrityResult(
            ok=False,
            restored=False,
            prompt_path=prompt_path,
            message=f"{prompt_path} is missing.",
        )

    content = workspace.read(prompt_path)
    if not content.strip():
        return IntegrityResult(
            ok=False,
            restored=False,
            prompt_path=prompt_path,
            message=f"{prompt_path} is empty.",
        )

    return IntegrityResult(
        ok=True,
        restored=False,
        prompt_path=prompt_path,
        message=f"{prompt_path} is present and non-empty.",
    )


def find_prompt_backup(
    workspace: Workspace,
    *,
    backup_paths: tuple[str, ...] = DEFAULT_BACKUP_PATHS,
) -> str | None:
    """Return the first available prompt backup path."""
    for candidate in backup_paths:
        if workspace.exists(candidate) and workspace.read(candidate).strip():
            return candidate
    return None


def ensure_prompt_integrity(
    workspace: Workspace,
    *,
    phase: str,
    iteration: int,
    prompt_path: str | None = None,
    backup_paths: tuple[str, ...] = DEFAULT_BACKUP_PATHS,
) -> IntegrityResult:
    """Ensure PROMPT.md is present, restoring from backup when possible.

    When ``prompt_path`` is ``None`` the effective path is resolved
    through
    :func:`ralph.pro_support.prompt.resolve_effective_prompt_path`.
    """
    if prompt_path is None:
        prompt_path = str(resolve_effective_prompt_path(workspace.absolute_path("."), os.environ))
    verification = verify_prompt_integrity(workspace, prompt_path=prompt_path)
    if verification.ok:
        return verification

    backup_path = find_prompt_backup(workspace, backup_paths=backup_paths)
    if backup_path is None:
        return IntegrityResult(
            ok=False,
            restored=False,
            prompt_path=prompt_path,
            message=(
                f"{prompt_path} failed integrity during {phase} phase "
                f"(iteration {iteration}) and no backup was available."
            ),
        )

    workspace.write(prompt_path, workspace.read(backup_path))
    return IntegrityResult(
        ok=True,
        restored=True,
        prompt_path=prompt_path,
        backup_path=backup_path,
        message=(
            f"{prompt_path} was restored from {backup_path} during {phase} phase "
            f"(iteration {iteration})."
        ),
    )


__all__ = [
    "DEFAULT_BACKUP_PATHS",
    "DEFAULT_PROMPT_PATH",
    "IntegrityResult",
    "default_prompt_path",
    "ensure_prompt_integrity",
    "find_prompt_backup",
    "verify_prompt_integrity",
]
