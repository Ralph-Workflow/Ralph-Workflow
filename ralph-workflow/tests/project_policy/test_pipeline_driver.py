"""The policy pipeline's state machine: routing, the hard gate, and the budget.

These tests drive the REAL deterministic validator against a MemoryWorkspace. A
fake agent "fixes" the policy by seeding a corpus that genuinely passes
``validate_readiness``, so the hard gate under test is the real one rather than a
mock of it.
"""

from __future__ import annotations

import pytest

from ralph.project_policy import analysis, pipeline_driver, remediation
from ralph.project_policy.models import PolicyFinding, ReadinessStatus
from ralph.project_policy.pipeline_graph import (
    DEFAULT_ANALYSIS_CAP,
    PHASE_ANALYSIS,
    PHASE_REMEDIATION,
)
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack


def _finding() -> PolicyFinding:
    return PolicyFinding(
        requirement_id="RWP-CORE:testing-policy.md",
        path="docs/ralph-workflow-policy/testing-policy.md",
        missing_evidence="file does not exist",
        required_outcome="create it",
    )


def _submit_decision(
    workspace: MemoryWorkspace,
    status: str,
    *,
    what_came_up_short: list[str] | None = None,
) -> None:
    """Write the decision artifact the way the MCP submit path would."""
    workspace.mkdirs(".agent/artifacts")
    shortcomings = what_came_up_short or []
    extra_sections = ""
    if shortcomings:
        short_items = "\n".join(
            f"- [W-{index}] {item}" for index, item in enumerate(shortcomings, start=1)
        )
        extra_sections = (
            f"\n## What Came Up Short\n\n{short_items}\n"
            "\n## How To Fix\n\n- [FIX-1] Do the thing.\n"
        )
    workspace.write(
        analysis.ANALYSIS_ARTIFACT_REL_PATH,
        (
            "---\n"
            f"type: {analysis.ANALYSIS_ARTIFACT_TYPE}\n"
            f"status: {status}\n"
            "---\n\n"
            "## Summary\n\n"
            "- [SUM-1] Review complete.\n"
            f"{extra_sections}"
        ),
    )


class _Recorder:
    """A fake agent chain that records the phase sequence it was driven through."""

    def __init__(
        self,
        workspace: MemoryWorkspace,
        *,
        decisions: list[str],
        fix_on_call: int | None = 1,
    ) -> None:
        self.workspace = workspace
        self.decisions = list(decisions)
        self.fix_on_call = fix_on_call
        self.phases: list[str] = []
        self._remediation_calls = 0

    def __call__(self, *, phase: str, prompt_path: str) -> bool:
        del prompt_path
        self.phases.append(phase)
        if phase == PHASE_REMEDIATION:
            self._remediation_calls += 1
            if (
                self.fix_on_call is not None
                and self._remediation_calls >= self.fix_on_call
            ):
                seed_complete_corpus(self.workspace)
            return True
        decision = self.decisions.pop(0) if self.decisions else "request_changes"
        _submit_decision(self.workspace, decision)
        return True

    @property
    def trace(self) -> str:
        """The phase sequence as 'R A R A ...' for readable assertions."""
        return " ".join(
            "R" if phase == PHASE_REMEDIATION else "A" for phase in self.phases
        )


def test_happy_path_is_one_remediation_then_one_analysis() -> None:
    ws = MemoryWorkspace()
    agent = _Recorder(ws, decisions=["completed"])

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [_finding()], invoke_agent=agent
    )

    assert agent.trace == "R A"
    assert result.status is ReadinessStatus.READY


def test_exhausted_analysis_ends_on_a_final_remediation() -> None:
    """With cap=3, three rejections give R A R A R A R: four remediations, three
    analyses, and the LAST thing that happens is a remediation applying the final
    review's feedback. Then the run proceeds -- it does not fail."""
    ws = MemoryWorkspace()
    agent = _Recorder(ws, decisions=["request_changes"] * 4)

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [_finding()], invoke_agent=agent
    )

    assert agent.trace == "R A R A R A R"
    assert agent.phases.count(PHASE_ANALYSIS) == DEFAULT_ANALYSIS_CAP
    assert agent.phases[-1] == PHASE_REMEDIATION
    assert result.status is ReadinessStatus.BLOCKED


def test_analysis_is_never_consulted_while_the_validator_still_fails() -> None:
    """The hard gate. An agent that cannot fix the policy must never reach the
    analysis phase -- reviewing the QUALITY of a structurally invalid policy is
    wasted work, and a 'completed' from analysis must never be able to launder a
    failing validator into a pass."""
    ws = MemoryWorkspace()
    agent = _Recorder(ws, decisions=["completed"], fix_on_call=None)

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [_finding()], invoke_agent=agent
    )

    assert PHASE_ANALYSIS not in agent.phases, agent.trace
    assert result.status is ReadinessStatus.BLOCKED
    assert result.findings, "the still-open findings must be preserved"


def test_a_forged_completed_cannot_produce_ready_while_findings_remain() -> None:
    """Belt and braces on the hard gate: even if a decision artifact claiming
    'completed' is sitting on disk, a failing validator still wins."""
    ws = MemoryWorkspace()
    _submit_decision(ws, "completed")
    agent = _Recorder(ws, decisions=["completed"], fix_on_call=None)

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [_finding()], invoke_agent=agent
    )

    assert result.status is ReadinessStatus.BLOCKED


def test_request_changes_loops_back_and_carries_feedback_forward() -> None:
    """Analysis feedback must reach the next remediation prompt, or the loop just
    re-derives the same policy from the same findings and never converges."""
    ws = MemoryWorkspace()
    prompts: list[str] = []

    def agent(*, phase: str, prompt_path: str) -> bool:
        if phase == PHASE_REMEDIATION:
            seed_complete_corpus(ws)
            prompts.append(ws.read(prompt_path))
            return True
        _submit_decision(
            ws,
            "completed" if len(prompts) >= 2 else "request_changes",
            what_came_up_short=["make verify does not resolve"],
        )
        return True

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [_finding()], invoke_agent=agent
    )

    assert result.status is ReadinessStatus.READY
    assert len(prompts) == 2
    assert "make verify does not resolve" not in prompts[0]
    assert "make verify does not resolve" in prompts[1], (
        "the second remediation prompt must carry the reviewer's findings"
    )


def test_run_agents_entry_reviews_a_clean_policy_without_remediating() -> None:
    """--run-policy-agents enters at ANALYSIS. A policy that is already good is
    approved without a single remediation agent touching it."""
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    agent = _Recorder(ws, decisions=["completed"])

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [], invoke_agent=agent, entry_phase=PHASE_ANALYSIS
    )

    assert agent.trace == "A"
    assert PHASE_REMEDIATION not in agent.phases, "nothing may be overwritten"
    assert result.status is ReadinessStatus.READY


def test_run_agents_entry_routes_into_remediation_when_the_audit_fails() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    agent = _Recorder(ws, decisions=["request_changes", "completed"])

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [], invoke_agent=agent, entry_phase=PHASE_ANALYSIS
    )

    assert agent.trace == "A R A"
    assert result.status is ReadinessStatus.READY


@pytest.mark.parametrize("cap", [1, 2, 5])
def test_analysis_invocations_never_exceed_the_cap(cap: int) -> None:
    ws = MemoryWorkspace()
    agent = _Recorder(ws, decisions=["request_changes"] * (cap + 3))

    pipeline_driver.run_policy_pipeline(
        ws, stack(), [_finding()], invoke_agent=agent, analysis_cap=cap
    )

    assert agent.phases.count(PHASE_ANALYSIS) == cap
    assert agent.phases.count(PHASE_REMEDIATION) == cap + 1


def test_a_launch_crash_in_the_analysis_phase_does_not_escape_the_driver() -> None:
    """REGRESSION (found by review). The launch-crash handler only wrapped the
    remediation branch, so a RemediationInvocationError raised while launching the
    ANALYSIS agent propagated out of the driver instead of degrading to
    'not ready'. The run still survived (the outer fault boundary caught it), but
    the driver lost the findings list and the specific launch-failure report."""
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)

    def invoke(*, phase: str, prompt_path: str) -> bool:
        del prompt_path
        if phase == PHASE_ANALYSIS:
            raise remediation.RemediationInvocationError("agent binary not found")
        return True

    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [], invoke_agent=invoke, entry_phase=PHASE_ANALYSIS
    )

    assert result.status is ReadinessStatus.BLOCKED
    assert any("could not be launched" in line for line in result.report_lines)


def test_an_analysis_entry_with_a_spent_budget_still_terminates() -> None:
    """REGRESSION (found by review). Entering at ANALYSIS with cap=0 skipped the
    budget check and ran one analysis anyway, so the cap was not a true invariant
    on analysis invocations."""
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    agent = _Recorder(ws, decisions=["completed"])

    result = pipeline_driver.run_policy_pipeline(
        ws,
        stack(),
        [],
        invoke_agent=agent,
        entry_phase=PHASE_ANALYSIS,
        analysis_cap=0,
    )

    assert agent.phases == [], "a spent budget must invoke no analysis agent at all"
    assert result.status is ReadinessStatus.BLOCKED
def test_on_remediation_attempt_callback_receives_live_attempt_numbers() -> None:
    """AC-01 / AC-03: the persistent status bar must see the LIVE attempt value.

    ``on_remediation_attempt`` is invoked BEFORE each remediation with the
    1-indexed attempt number so the caller can push the right
    ``Remediation N/Max`` label. Two consecutive remediation attempts
    must produce ``1`` then ``2`` -- not ``1`` for both (the regression
    that motivated the callback).
    """
    ws = MemoryWorkspace()
    agent = _Recorder(ws, decisions=["request_changes", "completed"])

    seen_attempts: list[int] = []

    def _on_attempt(attempt: int) -> None:
        seen_attempts.append(attempt)

    result = pipeline_driver.run_policy_pipeline(
        ws,
        stack(),
        [_finding()],
        invoke_agent=agent,
        on_remediation_attempt=_on_attempt,
    )

    assert result.status is ReadinessStatus.READY
    remediation_count = agent.phases.count(PHASE_REMEDIATION)
    assert remediation_count >= 2, (
        "the test fixture must drive at least two remediation attempts"
    )
    # One callback per remediation, in 1-indexed order.
    assert len(seen_attempts) == remediation_count
    assert seen_attempts == list(range(1, remediation_count + 1))
    # Specifically: the first attempt is 1, the second is 2 (the regression).
    assert seen_attempts[0] == 1
    assert seen_attempts[1] == 2


def test_on_remediation_attempt_callback_defaults_to_noop() -> None:
    """Existing callers (no callback) keep working unchanged (AC-01)."""
    ws = MemoryWorkspace()
    agent = _Recorder(ws, decisions=["completed"])

    # No on_remediation_attempt kwarg.
    result = pipeline_driver.run_policy_pipeline(
        ws, stack(), [_finding()], invoke_agent=agent
    )

    assert result.status is ReadinessStatus.READY
