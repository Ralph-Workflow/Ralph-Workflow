"""ProcessExecutor Protocol for running external git processes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ralph.git.rebase.process_result import ProcessResult


class ProcessExecutor(Protocol):
    """Executor that runs external processes."""

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult: ...


__all__ = ["ProcessExecutor"]
