from __future__ import annotations

from ralph.project_policy import analysis, pipeline_driver
from ralph.project_policy.models import ReadinessStatus
from ralph.project_policy.pipeline_graph import PHASE_ANALYSIS, PHASE_REMEDIATION
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack


def _completed(workspace: MemoryWorkspace) -> None:
    workspace.mkdirs(".agent/artifacts")
    workspace.write(
        analysis.ANALYSIS_ARTIFACT_REL_PATH,
        "---\n"
        f"type: {analysis.ANALYSIS_ARTIFACT_TYPE}\n"
        "status: completed\n"
        "---\n\n"
        "## Summary\n\n"
        "- [SUM-1] Review complete. Evidence: declared policy probes were inspected.\n"
        "\n## Criterion Verdicts\n\n"
        "- [PR-001] Criterion: declared policy fact holds. Expected observation: the declared "
        "probe resolves. Verdict: met. Evidence: declared policy probes were inspected. "
        "Location: policy declaration.\n",
    )


class _Agent:
    def __init__(self, workspace: MemoryWorkspace, *, analysis_submits: list[bool]) -> None:
        self.workspace = workspace
        self.analysis_submits = list(analysis_submits)
        self.phases: list[str] = []

    def __call__(self, *, phase: str, prompt_path: str, progress_check: object = None) -> bool:
        del prompt_path, progress_check
        self.phases.append(phase)
        if phase == PHASE_ANALYSIS and self.analysis_submits and self.analysis_submits.pop(0):
            _completed(self.workspace)
        return True


def test_an_analysis_agent_that_submits_nothing_is_retried_not_sent_to_remediation() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    agent = _Agent(ws, analysis_submits=[False, True])

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [], invoke_agent=agent, entry_phase=PHASE_ANALYSIS
    )

    assert PHASE_REMEDIATION not in agent.phases
    assert agent.phases == [PHASE_ANALYSIS, PHASE_ANALYSIS]
    assert result.status is ReadinessStatus.READY


def test_an_analysis_agent_that_never_submits_ends_readiness_without_remediating() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    agent = _Agent(ws, analysis_submits=[])

    pipeline_driver.run_policy_pipeline(
        ws, stack(), [], invoke_agent=agent, entry_phase=PHASE_ANALYSIS
    )

    assert PHASE_REMEDIATION not in agent.phases
    assert agent.phases == [PHASE_ANALYSIS, PHASE_ANALYSIS]
