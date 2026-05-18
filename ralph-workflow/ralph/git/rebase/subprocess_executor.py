"""Default SubprocessExecutor powered by run_git."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.rebase.process_result import ProcessResult
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class SubprocessExecutor:
    """Default executor powered by run_git."""

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        subcommand = args[0] if args else "unknown"
        result = run_git(
            args,
            cwd=cwd,
            label=f"git-rebase:{subcommand}",
            options=GitRunOptions(env=env),
        )
        return ProcessResult(
            returncode=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
