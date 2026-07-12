"""Tests for the synchronous remediation driver."""

from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, preflight, remediation
from ralph.project_policy.models import PolicyFinding, ReadinessStatus
from ralph.workspace.memory import MemoryWorkspace


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python")


def _stub_finding() -> PolicyFinding:
    return PolicyFinding(
        requirement_id="RWP-AGENTS-MD:missing",
        path=markers.AGENTS_MD,
        missing_evidence="AGENTS.md is missing",
        required_outcome="create AGENTS.md",
    )


def test_remediation_prompt_written_to_workspace_seam() -> None:
    """The remediation prompt is materialized via workspace.write (no .agent/artifacts write)."""
    ws = MemoryWorkspace()
    finding = _stub_finding()

    def fake_invoke(prompt_path: str) -> bool:
        # The agent does not fix anything.
        return False

    remediation.remediate(
        ws, _stack(), [finding], invoke_remediation_agent=fake_invoke, max_attempts=1
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


def test_invocation_error_aborts_loop_without_burning_budget() -> None:
    """An infrastructure failure (agent cannot launch) aborts the loop immediately.

    Retrying a deterministic launch crash burns the whole attempt budget in
    milliseconds and floods the display with one failure panel per attempt.
    The driver must stop after the first such crash and return BLOCKED.
    """
    ws = MemoryWorkspace()
    finding = _stub_finding()
    calls: list[str] = []
    emitted: list[str] = []

    def fake_invoke(prompt_path: str) -> bool:
        calls.append(prompt_path)
        raise remediation.RemediationInvocationError(
            "display_context is required when display is None"
        )

    result = remediation.remediate(
        ws,
        _stack(),
        [finding],
        invoke_remediation_agent=fake_invoke,
        max_attempts=200,
        emit=emitted.append,
    )
    assert result.is_blocked()
    assert len(calls) == 1
    assert len(emitted) <= 3
    assert any("could not be launched" in line for line in emitted)


def test_revalidation_gates_completion() -> None:
    """A fake agent that claims success but leaves findings must NOT mark READY."""
    ws = MemoryWorkspace()
    finding = _stub_finding()
    invoked_paths: list[str] = []

    def fake_invoke(prompt_path: str) -> bool:
        invoked_paths.append(prompt_path)
        return True

    result = remediation.remediate(
        ws,
        _stack(),
        [finding],
        invoke_remediation_agent=fake_invoke,
        max_attempts=2,
    )
    # Agent was invoked exactly once per attempt.
    assert len(invoked_paths) == 2
    # Revalidation gated completion: agent claim of success cannot mark READY.
    assert result.is_blocked()
    # Findings are preserved with stable id/path/evidence/outcome.
    assert any(f.requirement_id == finding.requirement_id for f in result.findings)


def test_agent_that_fixes_findings_yields_ready() -> None:
    """A fake agent that satisfies every validator requirement -> revalidation passes."""
    from tests.project_policy.test_validator import (
        _seed_all_core_complete,
    )

    ws = MemoryWorkspace()
    finding = _stub_finding()

    def fake_invoke(prompt_path: str) -> bool:
        # The "agent" satisfies every validator requirement on its first call.
        ws.write(
            markers.AGENTS_MD,
            f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
        )
        ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n")
        _seed_all_core_complete(ws, _stack())
        return True

    result = remediation.remediate(
        ws,
        _stack(),
        [finding],
        invoke_remediation_agent=fake_invoke,
        max_attempts=1,
    )
    assert result.is_ready()


def test_no_write_to_artifacts_issues_json() -> None:
    """The non-artifact handoff contract: no .agent/artifacts/issues.json write."""
    ws = MemoryWorkspace()

    def fake_invoke(prompt_path: str) -> bool:
        return False

    remediation.remediate(
        ws, _stack(), [_stub_finding()], invoke_remediation_agent=fake_invoke, max_attempts=1
    )
    assert not ws.exists(".agent/artifacts/issues.json")
    assert not ws.exists(".agent/artifacts/commit_message.json")
    assert not ws.exists(".agent/artifacts/development_result.json")


def test_remediation_with_zero_max_attempts_returns_blocked() -> None:
    ws = MemoryWorkspace()

    def fake_invoke(prompt_path: str) -> bool:  # pragma: no cover - unreachable
        raise AssertionError("invoke must not be called when max_attempts=0")

    result = remediation.remediate(
        ws,
        _stack(),
        [_stub_finding()],
        invoke_remediation_agent=fake_invoke,
        max_attempts=0,
    )
    assert result.status == ReadinessStatus.BLOCKED


def test_remediation_emits_one_line_per_attempt() -> None:
    ws = MemoryWorkspace()
    messages: list[str] = []

    def fake_invoke(prompt_path: str) -> bool:
        return False

    remediation.remediate(
        ws,
        _stack(),
        [_stub_finding()],
        invoke_remediation_agent=fake_invoke,
        max_attempts=2,
        emit=messages.append,
    )
    # At least one message per attempt plus a final message.
    assert any("attempt 1/2" in m for m in messages)
    assert any("attempt 2/2" in m for m in messages)


def test_remediation_uses_serde_findings_after_each_revalidation() -> None:
    """After each attempt, the loop re-serializes the CURRENT findings."""
    ws = MemoryWorkspace()
    seen_in_prompts: list[str] = []

    def fake_invoke(prompt_path: str) -> bool:
        # Capture the findings block from the prompt.
        content = ws.read(prompt_path)
        seen_in_prompts.append(content)
        return False

    remediation.remediate(
        ws,
        _stack(),
        [_stub_finding()],
        invoke_remediation_agent=fake_invoke,
        max_attempts=2,
    )
    # The first prompt references the original finding.
    assert "RWP-AGENTS-MD:missing" in seen_in_prompts[0]


def test_remediation_reserializes_still_open_findings() -> None:
    """When the agent partially fixes (some findings closed, others remain), the
    second prompt carries only the still-open findings."""
    ws = MemoryWorkspace()
    seen_in_prompts: list[str] = []

    def fake_invoke(prompt_path: str) -> bool:
        seen_in_prompts.append(ws.read(prompt_path))
        # On the second attempt, also create AGENTS.md (closes one finding) but
        # leave the others open.
        if len(seen_in_prompts) == 1:
            ws.write(
                markers.AGENTS_MD,
                f"{markers.AGENTS_BLOCK_BEGIN}\n{markers.CANONICAL_DIR}\n{markers.AGENTS_BLOCK_END}\n",
            )
        return True

    # Provide two findings; the first is closed by the agent, the second remains.
    finding_agents = PolicyFinding(
        requirement_id="RWP-AGENTS-MD:missing",
        path=markers.AGENTS_MD,
        missing_evidence="missing AGENTS.md",
        required_outcome="create AGENTS.md",
    )
    finding_other = PolicyFinding(
        requirement_id="RWP-CLAUDE-MD:missing",
        path=markers.CLAUDE_MD,
        missing_evidence="missing CLAUDE.md",
        required_outcome="create CLAUDE.md",
    )
    result = remediation.remediate(
        ws,
        _stack(),
        [finding_agents, finding_other],
        invoke_remediation_agent=fake_invoke,
        max_attempts=2,
    )
    # Second prompt must reference the still-open finding (CLAUDE.md) but not
    # the closed one (AGENTS.md).
    assert "RWP-CLAUDE-MD:missing" in seen_in_prompts[1]
    assert "RWP-AGENTS-MD:missing" not in seen_in_prompts[1]
    # Status is BLOCKED because the CLAUDE.md finding still remains.
    assert result.is_blocked()
    assert any(f.requirement_id == "RWP-CLAUDE-MD:missing" for f in result.findings)
