"""Prompt contracts for project-policy analysis."""

from __future__ import annotations

from ralph.project_policy import analysis, markers
from ralph.workspace.memory import MemoryWorkspace


def _prompt() -> str:
    return analysis._render_prompt(MemoryWorkspace())


def test_analysis_prompt_starts_with_the_review_instruction() -> None:
    prompt = _prompt()

    first_nonempty = next(line for line in prompt.splitlines() if line.strip())

    assert first_nonempty == "Judge the policy build system with fresh evidence and submit one decision artifact."
    assert prompt.index(first_nonempty) < prompt.index("*** UNATTENDED MODE")


def test_analysis_prompt_names_every_supported_transport_tool() -> None:
    prompt = _prompt()

    for tool_name in (
        "ralph_submit_md_artifact",
        "mcp__ralph__ralph_submit_md_artifact",
        "ralph_ralph_submit_md_artifact",
        "ralph_verify_md_artifact",
        "mcp__ralph__ralph_verify_md_artifact",
        "ralph_ralph_verify_md_artifact",
        "declare_complete",
        "mcp__ralph__declare_complete",
        "ralph_declare_complete",
    ):
        assert tool_name in prompt
    assert "for Claude, Claude Interactive, Codex, or Cursor" in prompt
    assert "for OpenCode" in prompt
    assert "for AGY, Pi, Nanocoder, or generic transports" in prompt


def test_analysis_prompt_accepts_truthful_pending_markers() -> None:
    prompt = _prompt()

    assert "truthful deferral instead of probing its deferred gate" in prompt
    assert "A truthful `RALPH-PENDING` marker is compatible with `completed`" in prompt
    assert "resolved `RALPH-FACT:`" in prompt


def test_analysis_prompt_names_the_approved_pending_gate_tools() -> None:
    normalized = " ".join(_prompt().split())
    approved_segment = normalized.split("approved first tokens:", 1)[1].split(")", 1)[0]
    advertised = {name.strip() for name in approved_segment.split(",")}

    assert advertised == set(markers.APPROVED_GATE_TOOLS)


def test_analysis_prompt_requires_truthful_failure_attribution() -> None:
    prompt = _prompt()
    normalized = " ".join(prompt.split())

    assert "Record the attribution; do not request changes" in normalized
    assert "IGNORE IT" not in prompt
    assert "Whatever exit code it returns is fine" not in prompt


def test_analysis_prompt_uses_subagents_for_independent_evidence() -> None:
    prompt = _prompt()

    assert "subagent" in prompt.lower()
    assert "read-only subagents for independent evidence when helpful" in prompt
    assert "main session" in prompt.lower()


def test_analysis_prompt_teaches_complete_and_remediation_invariants() -> None:
    normalized = " ".join(_prompt().split())

    assert "A completed decision omits both remediation sections; every known gap uses a non-completed status." in normalized
    assert "The two remediation ID sets must match exactly: no missing, extra, or mismatched gap/fix IDs." in normalized
    assert "For `request_changes` and `failed`, include non-empty `## What Came Up Short` and `## How To Fix` sections" in normalized


def test_analysis_prompt_keeps_declare_complete_as_the_final_action() -> None:
    prompt = _prompt()
    declaration_index = prompt.rindex("After a valid artifact submission receipt")

    assert declaration_index > prompt.rindex("The two remediation ID sets must match exactly")
    assert "as your final explicit action" in prompt[declaration_index:]
    assert prompt[declaration_index:].rstrip().endswith("generic transports.")
