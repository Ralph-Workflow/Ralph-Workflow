"""Starters must read as durable enforcement policy, not remediation to-dos.

The remediation prompt (:mod:`ralph.project_policy.remediation`) is the single
home for remediation-time instructions (inspect the project, replace
placeholders, gate the completion marker). Policy starters become the
project's PERMANENT policy files after remediation, so any sentence that only
makes sense while remediating is forbidden in them: it would bloat every
project's policy files forever and bury the instructions agents actually need
to FOLLOW the policy.
"""

from __future__ import annotations

import pytest

from ralph.project_policy import remediation, starters

#: Phrases that address the remediation agent's one-time task rather than the
#: permanent policy reader. Each must live in the remediation prompt (most
#: already do) and must NOT appear in any starter.
_REMEDIATION_ERA_PHRASES: tuple[str, ...] = (
    # One-time fill-in instructions.
    "REPLACE every starter placeholder",
    "The agent MUST resolve every",
    "resolve every `RALPH-FACT:` line",
    "REFUSE to add the completion marker",
    "REMOVE inapplicable conditional sections",
    "INSPECT the project to identify",
    "PRESERVE stricter existing",
    # Validator mechanics addressed to the remediating agent.
    "the validator will reject",
    "rejected by the validator",
    "is rejected by the validator",
    "Placeholder tokens in the",
    # Stale conditional-seeding mechanics: conditional starters are only
    # seeded when their domain is detected, so "REMOVE this file" and
    # validator-signal explanations are never true in a seeded file.
    "REMOVE this file",
    "validator detects",
)


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_contains_no_remediation_era_prose(name: str) -> None:
    content = starters.read_starter(name)
    violations = [
        phrase for phrase in _REMEDIATION_ERA_PHRASES if phrase in content
    ]
    assert not violations, (
        f"starter {name} contains remediation-era prose that would live "
        f"forever in the project's policy file: {violations}. Move the "
        f"instruction into the remediation prompt instead."
    )


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_facts_section_reads_as_a_record(name: str) -> None:
    """The facts section must present RALPH-FACT lines as a durable record
    agents rely on and keep current — not as a to-do list to 'resolve'."""
    content = starters.read_starter(name)
    assert "verified project facts" in content.lower(), (
        f"starter {name}: the facts section must frame RALPH-FACT lines as "
        "a record of verified project facts"
    )


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_carries_exactly_one_template_banner(name: str) -> None:
    """Each starter must be OBVIOUSLY a template: one RALPH-STARTER-TEMPLATE
    banner comment whose removal is machine-enforced (the token is a
    validator placeholder, so readiness is blocked until it is deleted)."""
    content = starters.read_starter(name)
    assert content.count("RALPH-STARTER-TEMPLATE") == 1, (
        f"starter {name} must contain exactly one template banner"
    )


def test_validator_blocks_readiness_while_banner_remains() -> None:
    """A freshly seeded starter must yield a deterministic finding for the
    template banner, and an otherwise-complete policy file that still
    carries the banner must NOT validate clean."""
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import markers, validators
    from ralph.workspace.memory import MemoryWorkspace
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    stack = ProjectStack(primary_language="Python")

    # Complete project: no findings.
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, stack)
    assert validators.validate_readiness(ws, stack) == []

    # Same project with the banner re-inserted into one complete file.
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(
        path,
        "<!-- RALPH-STARTER-TEMPLATE: template banner -->\n" + ws.read(path),
    )
    findings = validators.validate_readiness(ws, stack)
    assert any(
        markers.STARTER_TEMPLATE_TOKEN in finding.missing_evidence
        for finding in findings
    ), "an otherwise-complete file carrying the banner must be flagged"


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_machine_lines_are_line_start(name: str) -> None:
    """RALPH machine lines must sit at line start: the validator only counts
    line-anchored `RALPH-FACT:` / `RALPH-COMMAND:` / `RALPH-LANG:` /
    `RALPH-INAPPLICABLE:` lines, so a bulleted machine line can never
    satisfy a check even after its value is filled in."""
    content = starters.read_starter(name)
    offending = [
        line
        for line in content.splitlines()
        if any(
            f"RALPH-{kind}:" in line
            for kind in ("FACT", "COMMAND", "LANG", "INAPPLICABLE")
        )
        and line.lstrip().startswith(("*", "-"))
        and line.lstrip("*- ").startswith("RALPH-")
    ]
    assert not offending, (
        f"starter {name} has bulleted machine lines the validator will "
        f"never count: {offending}"
    )


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_filled_in_starter_validates_clean(name: str) -> None:
    """The full contract: take a starter, do exactly what the remediation
    prompt says (resolve placeholders in place, delete the banner, add the
    completion marker) and the file must pass every per-file validator
    check. If this fails, the template forces the agent to fight the
    validator over formatting instead of filling in facts."""
    from ralph.project_policy import markers, validators
    from ralph.workspace.memory import MemoryWorkspace

    content = starters.read_starter(name)
    # Delete the banner comment block.
    start = content.find("<!-- RALPH-STARTER-TEMPLATE")
    assert start != -1
    end = content.find("-->", start) + len("-->")
    content = content[:start] + content[end:].lstrip("\n")
    # Resolve every command placeholder with an allowlisted tool, then every
    # remaining fact placeholder with a plain verified value.
    content = content.replace(
        "RALPH-COMMAND: PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test"
    )
    content = content.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    content += f"\n{markers.COMPLETION_MARKER}\n"

    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}{name}"
    ws.write(path, content)
    findings = validators._check_policy_file(ws, path, name)
    assert findings == [], (
        f"filled-in starter {name} still fails validation: "
        f"{[(f.requirement_id, f.missing_evidence) for f in findings]}"
    )


def test_remediation_prompt_owns_the_fill_in_instructions() -> None:
    """The instructions removed from starters must exist in the remediation
    prompt so the remediating agent still receives them."""
    prompt = remediation._render_prompt([])
    assert "Replace every `RALPH-FACT:` line" in prompt
    assert "completion marker" in prompt
    assert "INSPECT the project" in prompt
    assert "Remove inapplicable conditional sections" in prompt
    assert "stricter" in prompt
    assert "RALPH-STARTER-TEMPLATE" in prompt
