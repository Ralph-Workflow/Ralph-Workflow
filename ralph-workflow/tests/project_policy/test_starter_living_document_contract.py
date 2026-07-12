"""Every starter policy carries the living-document contract.

The canonical policy files are living documents: they must evolve as the
project grows and as verified facts change. Two guardrails bound that
evolution:

* conflicts between starter boilerplate and the project's established
  policy resolve IN FAVOR OF the existing project policy (adapt the
  canonical file to the project, never the reverse);
* evolution must never subvert a policy's INTENT — weakening or deleting a
  requirement to make a failing change pass is forbidden.

These tests pin the contract section in every starter and its echo in the
remediation prompt and the condensed AGENTS.md block.
"""

from __future__ import annotations

from pathlib import Path

from ralph.project_policy import agents_md, markers, remediation
from ralph.project_policy.models import PolicyFinding
from ralph.workspace.memory import MemoryWorkspace

_STARTERS_DIR = (
    Path(__file__).resolve().parents[2] / "ralph" / "project_policy" / "starters"
)


def _starter_paths() -> list[Path]:
    paths = sorted(_STARTERS_DIR.glob("*.md"))
    assert paths, "starter templates must exist"
    return paths


def test_every_starter_declares_living_document_contract() -> None:
    for path in _starter_paths():
        content = path.read_text(encoding="utf-8")
        assert "## Living document contract" in content, path.name
        lowered = content.lower()
        assert "living document" in lowered, path.name
        assert "favor of the existing project policy" in lowered, path.name
        assert "intent" in lowered, path.name


def test_remediation_prompt_states_living_document_rules() -> None:
    finding = PolicyFinding(
        requirement_id="RWP-AGENTS-MD:missing",
        path=markers.AGENTS_MD,
        missing_evidence="AGENTS.md is missing",
        required_outcome="create AGENTS.md",
    )
    prompt = remediation._render_prompt([finding])
    lowered = prompt.lower()
    assert "living document" in lowered
    assert "favor of the existing project policy" in lowered
    assert "intent" in lowered


def test_condensed_agents_block_mentions_policies_evolve() -> None:
    ws = MemoryWorkspace()
    agents_md.bootstrap(ws)
    assert agents_md.condense_placeholder_block(ws) == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert "living document" in content.lower()
    begin = content.find(markers.AGENTS_BLOCK_BEGIN)
    end = content.find(markers.AGENTS_BLOCK_END)
    assert len(content[begin:end].splitlines()) <= 10, "block must stay short"
