from __future__ import annotations

__all__ = [
    "CommitAgentResult",
    "SmokeRunResult",
    "run_commit_plumbing",
    "run_smoke_plumbing",
]


def __getattr__(name: str) -> object:
    if name == "CommitAgentResult":
        from ralph.pipeline.plumbing.commit_plumbing import CommitAgentResult

        return CommitAgentResult
    if name == "run_commit_plumbing":
        from ralph.pipeline.plumbing.commit_plumbing import run_commit_plumbing

        return run_commit_plumbing
    if name == "SmokeRunResult":
        from ralph.pipeline.plumbing.smoke_plumbing import SmokeRunResult

        return SmokeRunResult
    if name == "run_smoke_plumbing":
        from ralph.pipeline.plumbing.smoke_plumbing import run_smoke_plumbing

        return run_smoke_plumbing
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
