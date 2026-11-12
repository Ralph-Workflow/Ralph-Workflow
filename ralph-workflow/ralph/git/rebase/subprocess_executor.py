from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.hardening import PINNED_CONFIG_ARGS, scrub_git_env
from ralph.git.rebase.process_result import ProcessResult
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class SubprocessExecutor:
    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        subcommand = args[0] if args else "unknown"
        pinned_args: list[str] = [*PINNED_CONFIG_ARGS, *args]
        result = run_git(
            pinned_args,
            cwd=cwd,
            label=f"git-rebase:{subcommand}",
            options=GitRunOptions(env=scrub_git_env(dict(env) if env else None)),
        )
        return ProcessResult(
            returncode=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
