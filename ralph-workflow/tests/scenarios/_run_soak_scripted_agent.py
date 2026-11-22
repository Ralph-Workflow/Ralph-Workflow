from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME
from tests.plan_fixtures import (
    MINIMAL_PLAN_MARKDOWN,
    commit_review_markdown,
    development_result_markdown,
)
from tests.scenarios._run_soak_agent_call import AgentCall

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig
    from tests.scenarios._run_soak import SoakRun
    from tests.scenarios._run_soak_observer_state import RunObserver

pytestmark = pytest.mark.subprocess_e2e


@dataclass
class ScriptedAgent:
    soak: SoakRun
    observer: RunObserver
    calls: Counter[str] = field(default_factory=Counter)
    phases: list[str] = field(default_factory=list)

    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del config, _clock
        prompt_path = Path(prompt_file)
        if prompt_path.name == CANARY_PROMPT_FILENAME:
            return iter(["ok\n"])
        phase = (options.phase if options is not None else None) or prompt_path.stem
        self.calls[phase] += 1
        self.phases.append(phase)
        absolute = prompt_path if prompt_path.is_absolute() else self.soak.root / prompt_path
        prompt = absolute.read_text(encoding="utf-8") if absolute.is_file() else ""
        call = AgentCall(phase, self.calls[phase], prompt)
        self.observer.agent(call)
        hook = self.soak.hooks.get((phase, call.nth))
        if hook is None or not hook(self.soak, call):
            self._act(call)
        return iter([f"{phase} done\n"])

    def _act(self, call: AgentCall) -> None:
        from tests.scenarios._run_soak import (
            _DEVELOPMENT_DECISION,
            _PLANNING_DECISION,
            WORK_FILE,
            _commit_message,
        )

        root = self.soak.root
        artifacts = root / ".agent" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        if call.phase == "planning":
            (artifacts / "plan.md").write_text(MINIMAL_PLAN_MARKDOWN, encoding="utf-8")
        elif call.phase == "planning_analysis":
            (artifacts / "planning_analysis_decision.md").write_text(
                _PLANNING_DECISION, encoding="utf-8"
            )
        elif call.phase == "development_analysis":
            (artifacts / "development_analysis_decision.md").write_text(
                _DEVELOPMENT_DECISION, encoding="utf-8"
            )
        elif call.phase == "development":
            (root / WORK_FILE).write_text(f"VALUE = {call.nth}\n", encoding="utf-8")
            if "Publication gate" in call.prompt:
                (root / "gate-ok").write_text("fixed\n", encoding="utf-8")
            (artifacts / "development_result.md").write_text(
                development_result_markdown("example"), encoding="utf-8"
            )
        elif call.phase.endswith("commit_review"):
            (artifacts / "commit_review.md").write_text(
                commit_review_markdown((), tree_sha=self.soak.commit_review_tree()),
                encoding="utf-8",
            )
        elif call.phase.endswith("commit"):
            (artifacts / "commit_message.md").write_text(
                _commit_message(f"feat: soak change {call.nth}"), encoding="utf-8"
            )
        else:
            self.soak.resolve_conflicts()
