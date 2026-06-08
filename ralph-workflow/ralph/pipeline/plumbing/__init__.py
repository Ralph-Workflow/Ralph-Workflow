"""Pipeline plumbing module: commit/--generate-commit orchestration extracted from CLI.

This package owns the chain-iteration and classifier-routing logic that
the ``commit`` CLI command needs. The CLI surface in
``ralph.cli.commands.commit`` calls into :mod:`run_commit_plumbing` and
stays thin (option parsing, output formatting, exit codes only).
"""

from __future__ import annotations

from ralph.pipeline.plumbing.commit_plumbing import (
    CommitAgentResult,
    run_commit_plumbing,
)

__all__ = ["CommitAgentResult", "run_commit_plumbing"]
