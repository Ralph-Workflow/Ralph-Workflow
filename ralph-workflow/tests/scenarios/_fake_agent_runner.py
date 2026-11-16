from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.events import PipelineEvent
from tests._scenario_agent_mode import AgentMode
from tests._scenario_git import git

if TYPE_CHECKING:
    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline.conflict_resolution import RebaseStop
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = ["FakeAgentRunner"]


@dataclass
class FakeAgentRunner:
    mode: AgentMode
    launches: int = 0
    launch_times: list[float] = field(default_factory=list)
    completed_stops: list[str] = field(default_factory=list)
    clock: FakeClock | None = None
    fail_at_launch: int | None = None
    resolve_shared_txt_with_target: bool = False

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: object,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        from ralph.pipeline.integration_actions import default_agent_runner

        effective_resolver = resolver if callable(resolver) else self
        return default_agent_runner().resolve_rebase(
            root, target, effective_resolver, session=session
        )

    def handle_agent_effect(self, effect: object, *args: object, **kwargs: object) -> object:
        self.launches += 1
        self.launch_times.append(self.clock.monotonic() if self.clock is not None else 0.0)
        workspace_scope = kwargs.get("workspace_scope")
        if workspace_scope is None and len(args) > 2:
            workspace_scope = args[2]
        root = getattr(workspace_scope, "root", Path.cwd())

        if self.mode is AgentMode.PROVIDER_FAILURE:
            raise ConnectionError("agent provider unavailable")
        if self.mode is AgentMode.WATCHDOG_HANG:
            raise TimeoutError("agent idle watchdog fired")
        if self.mode is AgentMode.NON_ZERO_EXIT:
            raise RuntimeError("agent exited with status 7")
        if self.mode is AgentMode.SIGNAL:
            raise RuntimeError("agent terminated by signal 15")
        if self.mode is AgentMode.INVALID_RESULT:
            return PipelineEvent.AGENT_FAILURE
        if self.mode is AgentMode.LEAVES_MARKER:
            return PipelineEvent.AGENT_SUCCESS

        from ralph.git.merge import unmerged_paths

        unmerged = unmerged_paths(root)
        for relative in unmerged:
            path = root / relative
            if path.is_file():
                if relative == "shared.txt" and (
                    (root / "series.txt").exists() or self.resolve_shared_txt_with_target
                ):
                    path.write_text("target value\n", encoding="utf-8")
                else:
                    path.write_text("resolved by scenario agent\n", encoding="utf-8")
        if self.mode is AgentMode.EDITS_AND_STAGES and unmerged:
            git(root, "add", "--", *unmerged)
        return PipelineEvent.AGENT_SUCCESS

    def __call__(self, root: Path, _target: str, stop: RebaseStop) -> bool:
        stop_identity: str | None = None
        if self.fail_at_launch is not None:
            stop_identity = git(root, "rev-parse", "REBASE_HEAD")
            if stop_identity in self.completed_stops:
                raise AssertionError("a completed stop must never be presented to the agent again")
            if self.fail_at_launch == (self.launches + 1):
                self.launches += 1
                self.launch_times.append(self.clock.monotonic() if self.clock is not None else 0.0)
                raise ConnectionError("agent provider unavailable partway through finite work")
        from ralph.agents.system_clock import SystemClock
        from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
        from ralph.pipeline.effect_executor import current_agent_effect_dependencies
        from ralph.pipeline.effects.invoke_agent_effect import InvokeAgentEffect
        from ralph.workspace.scope import WorkspaceScope

        injected = current_agent_effect_dependencies().agent_runner
        if injected is None:
            raise RuntimeError("scenario agent runner dependency unavailable")
        deps = AttemptRunnerDeps(
            clock=self.clock or SystemClock(),
            invoke=injected,
            probe_runner=None,
            workspace_probe=None,
            connectivity=None,
        )
        event = deps.invoke(
            InvokeAgentEffect(
                agent_name="scenario-rebase-resolver",
                phase="rebase_conflict_resolution",
                prompt_file="prompt.md",
            ),
            workspace_scope=WorkspaceScope(root),
        )
        if event == PipelineEvent.AGENT_FAILURE:
            return False
        if stop_identity is not None:
            self.completed_stops.append(stop_identity)
        return True
