"""Pipeline plumbing module: CLI-orchestration cores extracted from CLI surfaces.

This package owns the chain-iteration, classifier-routing, and smoke-run logic
that the ``commit`` and ``smoke`` CLI commands need. The CLI surfaces stay thin
(option parsing, report rendering, exit codes only).
"""

from __future__ import annotations

import importlib
from typing import Protocol, cast

__all__ = [
    "CommitAgentResult",
    "SmokeRunResult",
    "run_commit_plumbing",
    "run_smoke_plumbing",
]


class _CommitPlumbingModule(Protocol):
    CommitAgentResult: type[object]
    run_commit_plumbing: object


class _SmokePlumbingModule(Protocol):
    SmokeRunResult: type[object]
    run_smoke_plumbing: object


def __getattr__(name: str) -> object:
    if name == "CommitAgentResult":
        module = importlib.import_module("ralph.pipeline.plumbing.commit_plumbing")
        return cast("_CommitPlumbingModule", module).CommitAgentResult
    if name == "run_commit_plumbing":
        module = importlib.import_module("ralph.pipeline.plumbing.commit_plumbing")
        return cast("_CommitPlumbingModule", module).run_commit_plumbing
    if name == "SmokeRunResult":
        module = importlib.import_module("ralph.pipeline.plumbing.smoke_plumbing")
        return cast("_SmokePlumbingModule", module).SmokeRunResult
    if name == "run_smoke_plumbing":
        module = importlib.import_module("ralph.pipeline.plumbing.smoke_plumbing")
        return cast("_SmokePlumbingModule", module).run_smoke_plumbing
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
