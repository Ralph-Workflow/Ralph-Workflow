"""Value object for one detected global Ralph installation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.update_check._install_kind import InstallKind

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ExistingInstall:
    """A global executable and the installation type that owns it."""

    executable: Path
    package_file: Path
    kind: InstallKind

    @property
    def remove_command(self) -> tuple[str, ...] | None:
        """Return the safe package-manager removal command, when one exists."""
        if self.kind is InstallKind.PIPX:
            return ("pipx", "uninstall", "ralph-workflow")
        if self.kind is InstallKind.UV_TOOL:
            return ("uv", "tool", "uninstall", "ralph-workflow")
        return None
