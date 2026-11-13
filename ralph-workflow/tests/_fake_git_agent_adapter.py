from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop, RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

_GIT_OPTIONS = GitRunOptions(timeout=5)


class RealRepoAgentAdapter:
    def __init__(
        self,
        resolutions: Mapping[str, str | bytes],
        *,
        succeeds: bool = True,
        decisions: tuple[str, ...] = (),
    ) -> None:
        self.resolutions = dict(resolutions)
        self.succeeds = succeeds
        self.decisions = decisions
        self.launch_count = 0
        self.launched_targets: list[str] = []

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        del resolver, session
        return self._resolve(root, target)

    def resolve_stop(self, root: Path, target: str, stop: RebaseStop) -> bool:
        del stop
        return self._resolve(root, target)

    def _resolve(self, root: Path, target: str) -> bool:
        self.launch_count += 1
        self.launched_targets.append(target)
        if not self.succeeds:
            return False
        for relative, content in self.resolutions.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        paths = tuple(dict.fromkeys((*self.resolutions, *self.decisions)))
        if not paths:
            return True
        result = run_git(
            ("add", "--", *paths),
            cwd=root,
            label="fake-git-agent:stage-resolution",
            options=_GIT_OPTIONS,
        )
        return result.returncode == 0


__all__ = ["RealRepoAgentAdapter"]
