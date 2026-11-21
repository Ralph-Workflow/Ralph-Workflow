from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitHookRejected:
    hook_output: str
    tree_sha: str

    def feedback(self) -> str:
        return (
            f"The project's commit hook rejected the commit of staged tree {self.tree_sha}; "
            "nothing was committed. Change the content the hook output below reports so the "
            "hook accepts it. Never bypass the hook (for example with --no-verify) and never "
            f"weaken or edit the hook.\nHook output:\n{self.hook_output}"
        )


__all__ = ["CommitHookRejected"]
