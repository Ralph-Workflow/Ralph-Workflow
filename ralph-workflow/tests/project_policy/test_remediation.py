"""Tests for the remediation PHASE.

The LOOP that drives this phase (the budget, the analysis routing, the
exhausted-analysis bypass) moved to ralph.project_policy.pipeline_driver and is
tested in test_pipeline_driver.py. What remains here is the phase itself: the
prompt it materializes, and its one hard rule -- it ALWAYS revalidates, and
never reports the agent's own claim as the result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, preflight, remediation
from ralph.project_policy.analysis_decision import AnalysisDecision
from ralph.project_policy.models import PolicyFinding
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.project_policy.analysis import InvokePolicyAgent


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python")


def _stub_finding() -> PolicyFinding:
    return PolicyFinding(
        requirement_id="RWP-AGENTS-MD:missing",
        path=markers.AGENTS_MD,
        missing_evidence="AGENTS.md is missing",
        required_outcome="create AGENTS.md",
    )


def _agent(fix: Callable[[], None] | None = None) -> InvokePolicyAgent:
    """A fake remediation agent chain with the production keyword signature."""

    def invoke(*, phase: str, prompt_path: str) -> bool:
        del phase, prompt_path
        if fix is not None:
            fix()
        return True

    return invoke


def test_remediation_prompt_written_to_workspace_seam() -> None:
    """The remediation prompt is materialized via workspace.write (no .agent/artifacts write)."""
    ws = MemoryWorkspace()

    remediation.run_remediation_phase(
        ws, _stack(), [_stub_finding()], invoke_agent=_agent()
    )
    # Prompt is at the documented rel-path.
    assert ws.exists(preflight.REMEDIATION_PROMPT_REL_PATH)
    # No canonical-artifact write happens.
    assert not ws.exists(".agent/artifacts/issues.json")


def test_prompt_instructs_holistic_agents_md_discoverability() -> None:
    """The remediation prompt instructs the full discoverability chain, not
    just the individual findings: a consistent AGENTS.md managed block that
    routes AI agents to the canonical policy dir, plus the CLAUDE.md pointer."""
    prompt = remediation._render_prompt([_stub_finding()])
    assert markers.AGENTS_BLOCK_BEGIN in prompt
    assert markers.AGENTS_BLOCK_END in prompt
    assert markers.CANONICAL_DIR in prompt
    assert "AGENTS.md" in prompt
    assert "discover" in prompt.lower()
    # AGENTS.md must stay short: the bootstrap placeholder instructions are
    # replaced with a concise pointer once remediation completes.
    assert "concise" in prompt.lower()
    assert "short" in prompt.lower()
    # The block is INTEGRATED into the existing document, never left as a
    # bolted-on appended section.
    assert "integrate" in prompt.lower()
    assert "invisible" in prompt.lower()


def test_prompt_names_the_approved_gate_tool_allowlist() -> None:
    """The validator rejects any RALPH-COMMAND whose first token is not in
    APPROVED_GATE_TOOLS. The prompt must tell the agent this constraint and
    list the approved tools UP FRONT — otherwise the agent only discovers
    the allowlist by failing validation, burning one of the (default two)
    remediation attempts on a formatting rule it could not have known."""
    prompt = remediation._render_prompt([_stub_finding()])
    lowered = prompt.lower()
    assert "first" in lowered and "token" in lowered
    # The full allowlist is rendered so the agent can pick a compliant tool
    # without a failed round-trip.
    for tool in sorted(markers.APPROVED_GATE_TOOLS):
        assert tool in prompt, f"approved gate tool {tool!r} missing from prompt"


def test_prompt_never_advertises_fixture_shell_utilities() -> None:
    """The validator ACCEPTS shell utilities (test fixtures rely on them),
    but the prompt must never advertise them as approved gate tools: a weak
    agent picking from the advertised list could otherwise declare
    ``RALPH-COMMAND: echo ok`` and reach READY with zero real verification."""
    prompt = remediation._render_prompt([_stub_finding()])
    # Extract exactly the rendered tool list after "Approved tools:".
    segment = prompt.split("Approved tools:", 1)[1].split(".", 1)[0]
    advertised = {token.strip() for token in segment.replace("\n", " ").split(",")}
    assert advertised == set(markers.APPROVED_GATE_TOOLS)
    assert not advertised & set(markers.FIXTURE_GATE_UTILITIES)


def test_fixture_utilities_disjoint_from_advertised_tools() -> None:
    """The two allowlists must stay disjoint: an entry in both would be
    advertised while claiming fixture-only status."""
    assert not set(markers.APPROVED_GATE_TOOLS) & set(markers.FIXTURE_GATE_UTILITIES)


def test_prompt_steers_standard_community_files_to_migrated_marker() -> None:
    """Migration offers two resolutions: add the migrated marker, or remove
    the recognized heading. For standard community files (SECURITY.md,
    CONTRIBUTING.md) heading removal is the wrong choice — the file's
    location and heading are ecosystem conventions. The prompt must steer
    the agent to keep such files and use the marker."""
    prompt = remediation._render_prompt([_stub_finding()])
    assert "SECURITY.md" in prompt
    assert "CONTRIBUTING.md" in prompt
    assert "keep" in prompt.lower()


def test_launch_failure_propagates_to_the_driver() -> None:
    """A launch crash is infrastructure breakage, not a policy shortfall. The
    phase surfaces it; the DRIVER decides what to do (stop looping, and let the
    run continue anyway -- see test_policy_never_blocks_the_run.py)."""
    ws = MemoryWorkspace()

    def invoke(*, phase: str, prompt_path: str) -> bool:
        del phase, prompt_path
        raise remediation.RemediationInvocationError("agent binary not found")

    with pytest.raises(remediation.RemediationInvocationError):
        remediation.run_remediation_phase(
            ws, _stack(), [_stub_finding()], invoke_agent=invoke
        )


def test_the_phase_always_revalidates_and_never_trusts_the_agent() -> None:
    """THE RULE OF THIS MODULE. An agent that claims success but fixed nothing
    gets the same answer as an agent that gave up: the findings the DETERMINISTIC
    VALIDATOR reports afterward. The agent's own claim is never the return value."""
    ws = MemoryWorkspace()

    remaining = remediation.run_remediation_phase(
        ws, _stack(), [_stub_finding()], invoke_agent=_agent()
    )

    assert remaining, "a lying agent cannot produce an empty finding list"
    assert any("RWP-" in f.requirement_id for f in remaining)


def test_an_agent_that_really_fixes_the_project_yields_no_findings() -> None:
    ws = MemoryWorkspace()

    remaining = remediation.run_remediation_phase(
        ws,
        _stack(),
        [_stub_finding()],
        invoke_agent=_agent(lambda: seed_complete_corpus(ws)),
    )

    assert remaining == []


def test_the_phase_returns_the_still_open_findings_not_the_ones_it_was_given() -> None:
    """A partial fix must narrow the finding list, so the next prompt carries only
    what is still open rather than re-asking for work already done."""
    ws = MemoryWorkspace()

    def fix_agents_md_only() -> None:
        ws.write(
            markers.AGENTS_MD,
            f"{markers.AGENTS_BLOCK_BEGIN}\n{markers.CANONICAL_DIR}\n"
            f"{markers.AGENTS_BLOCK_END}\n",
        )

    remaining = remediation.run_remediation_phase(
        ws,
        _stack(),
        [_stub_finding()],
        invoke_agent=_agent(fix_agents_md_only),
    )

    ids = {f.requirement_id for f in remaining}
    assert "RWP-AGENTS-MD:missing" not in ids, "a closed finding must drop out"
    assert ids, "the still-open findings remain"


def test_no_write_to_canonical_artifact_paths() -> None:
    """The non-artifact handoff contract: the remediation drain is denied
    artifact.submit, so it must never leave an artifact JSON behind."""
    ws = MemoryWorkspace()

    remediation.run_remediation_phase(
        ws, _stack(), [_stub_finding()], invoke_agent=_agent()
    )

    assert not ws.exists(".agent/artifacts/issues.json")
    assert not ws.exists(".agent/artifacts/commit_message.json")
    assert not ws.exists(".agent/artifacts/development_result.json")


def test_analysis_feedback_reaches_the_prompt() -> None:
    """The reviewer's findings become the next author's task list."""
    ws = MemoryWorkspace()
    seen: list[str] = []

    def invoke(*, phase: str, prompt_path: str) -> bool:
        del phase
        seen.append(ws.read(prompt_path))
        return True

    remediation.run_remediation_phase(
        ws,
        _stack(),
        [_stub_finding()],
        invoke_agent=invoke,
        analysis_feedback=AnalysisDecision(
            status="request_changes",
            summary="the declared gate does not exist",
            what_came_up_short=["make verify is not a real target"],
            how_to_fix=["declare the real entry point"],
        ),
    )

    assert "make verify is not a real target" in seen[0]
    assert "declare the real entry point" in seen[0]
    assert "the declared gate does not exist" in seen[0]


def test_prompt_carries_the_original_findings() -> None:
    ws = MemoryWorkspace()
    seen: list[str] = []

    def invoke(*, phase: str, prompt_path: str) -> bool:
        del phase
        seen.append(ws.read(prompt_path))
        return False

    remediation.run_remediation_phase(
        ws, _stack(), [_stub_finding()], invoke_agent=invoke
    )

    assert "RWP-AGENTS-MD:missing" in seen[0]
