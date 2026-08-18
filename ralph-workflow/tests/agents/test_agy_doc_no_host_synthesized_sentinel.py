"""Doc-linter regression for the no-host-synthesized-sentinel contract.

The runtime contract is pinned by
``tests/test_agy_no_exemptions.py::test_agy_host_synthesized_sentinel_branch_is_gone``
(the host writes completion evidence for no transport, F7/DoD 19). These tests
pin the same contract in the user-facing docs so the deleted narration cannot
silently return. Narrow text-shaped assertions only; no behaviour assumptions.
"""

from __future__ import annotations

from pathlib import Path

DOC_PATHS = (
    Path("docs/sphinx/agent-compatibility.md"),
    Path("docs/agents/architecture.md"),
    Path("docs/sphinx/cli.md"),
    Path("docs/sphinx/mcp-tool-restriction.md"),
)

FORBIDDEN_PHRASES = (
    "wrote host-owned durable completion evidence",
    "Ralph Workflow writes the same host-owned durable sentinel",
    "host-owned durable completion evidence",
    "host completion sentinel",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNoHostSynthesizedSentinelDocs:
    """AGY docs must not narrate the host writing completion evidence."""

    def test_docs_contain_no_host_synthesized_sentinel_phrase(self) -> None:
        for path in DOC_PATHS:
            content = _read(path)
            for phrase in FORBIDDEN_PHRASES:
                assert phrase not in content, (
                    f"{path} must not contain the forbidden phrase {phrase!r}: "
                    "the host writes no completion evidence for any transport"
                )

    def test_agent_compatibility_states_agent_owned_completion_contract(self) -> None:
        content = _read(Path("docs/sphinx/agent-compatibility.md"))
        assert "declare_complete" in content, (
            "agent-compatibility.md must state the agent itself calls declare_complete"
        )
        assert "DEGRADED (absent)" in content, (
            "agent-compatibility.md must state a missed declare_complete call "
            "renders DEGRADED (absent)"
        )
        assert "host writes no completion evidence for any transport" in content

    def test_architecture_states_absent_completion_evidence_outcome(self) -> None:
        content = _read(Path("docs/agents/architecture.md"))
        assert "completion evidence was absent" in content, (
            "architecture.md must state that a missed MCP completion call leaves "
            "completion evidence absent"
        )
        assert "DEGRADED (absent)" in content, (
            "architecture.md must state the DEGRADED (absent) verdict for the "
            "missed-completion AGY smoke"
        )
