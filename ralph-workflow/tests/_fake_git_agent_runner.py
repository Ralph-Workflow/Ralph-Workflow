"""AgentRunner test implementation driven by SimulatedGitEnvironment."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from tests._fake_git_env import SimulatedGitEnvironment


class FakeAgentRunner:
    """AgentRunner implementation driven by SimulatedGitEnvironment."""

    def __init__(self, env: SimulatedGitEnvironment, *, can_resolve: bool = True) -> None:
        self.env = env
        self.can_resolve = can_resolve

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        self.env.dispatches.append("resolve_rebase")
        if not self.can_resolve:
            return False
        # Resolving in place produces a resolved feature tip
        self.env.feature_sha = f"{self.env.feature_sha}-resolved"
        self.env.head_sha = self.env.feature_sha
        return True
