from __future__ import annotations

import re

import pytest

from ralph.project_policy import remediation, starters

_REMEDIATION_ERA_PHRASES: tuple[str, ...] = (
    "REPLACE every starter placeholder",
    "The agent MUST resolve every",
    "resolve every `RALPH-FACT:` line",
    "REFUSE to add the completion marker",
    "REMOVE inapplicable conditional sections",
    "INSPECT the project to identify",
    "PRESERVE stricter existing",
    "the validator will reject",
    "rejected by the validator",
    "is rejected by the validator",
    "Placeholder tokens in the",
    "REMOVE this file",
    "validator detects",
    "starter boilerplate",
)


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_contains_no_remediation_era_prose(name: str) -> None:
    content = starters.read_starter(name)
    violations = [phrase for phrase in _REMEDIATION_ERA_PHRASES if phrase in content]
    assert not violations, (
        f"starter {name} contains remediation-era prose that would live "
        f"forever in the project's policy file: {violations}. Move the "
        f"instruction into the remediation prompt instead."
    )


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_facts_section_reads_as_a_record(name: str) -> None:
    content = starters.read_starter(name)
    start = content.find("<!-- RALPH-STARTER-TEMPLATE")
    if start != -1:
        end = content.find("-->", start) + len("-->")
        content = content[:start] + content[end:]
    assert "verified project facts" in content.lower(), (
        f"starter {name}: the facts section must frame RALPH-FACT lines as "
        "a record of verified project facts"
    )


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_carries_exactly_one_template_banner(name: str) -> None:
    content = starters.read_starter(name)
    assert content.count("RALPH-STARTER-TEMPLATE") == 1, (
        f"starter {name} must contain exactly one template banner"
    )


def test_validator_blocks_readiness_while_banner_remains() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import markers, validators
    from ralph.workspace.memory import MemoryWorkspace
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    stack = ProjectStack(primary_language="Python")

    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, stack)
    starters.seed_starter_into(ws, "policy-portfolio.toml")
    ws.write(
        markers.PORTFOLIO_PATH,
        ws.read(markers.PORTFOLIO_PATH).replace("PROJECT-FACT-UNRESOLVED", "make test"),
    )
    assert validators.validate_readiness(ws, stack) == []

    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(
        path,
        "<!-- RALPH-STARTER-TEMPLATE: template banner -->\n" + ws.read(path),
    )
    findings = validators.validate_readiness(ws, stack)
    assert any(
        markers.STARTER_TEMPLATE_TOKEN in finding.missing_evidence for finding in findings
    ), "an otherwise-complete file carrying the banner must be flagged"


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_machine_lines_are_line_start(name: str) -> None:
    content = starters.read_starter(name)
    offending = [
        line
        for line in content.splitlines()
        if any(
            f"RALPH-{kind}:" in line
            for kind in ("FACT", "COMMAND", "REVIEW", "LANG", "INAPPLICABLE")
        )
        and line.lstrip().startswith(("*", "-"))
        and line.lstrip("*- ").startswith("RALPH-")
    ]
    assert not offending, (
        f"starter {name} has bulleted machine lines the validator will never count: {offending}"
    )


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_filled_in_starter_validates_clean(name: str) -> None:
    from ralph.project_policy import markers, validators
    from ralph.workspace.memory import MemoryWorkspace

    content = starters.read_starter(name)
    for comment_prefix in ("<!-- RALPH-STARTER-TEMPLATE", "<!-- REPLACE-ME"):
        while (start := content.find(comment_prefix)) != -1:
            end = content.find("-->", start) + len("-->")
            content = content[:start] + content[end:].lstrip("\n")
    content = content.replace("RALPH-COMMAND: PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test")
    content = content.replace("deterministic: PROJECT-FACT-UNRESOLVED", "deterministic: make test")
    content = content.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    content = re.sub(
        r"^(RALPH-FACT: design_capture_command:).*$",
        r"\1 make visual-capture",
        content,
        flags=re.MULTILINE,
    )

    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}{name}"
    ws.write(path, content)
    findings = validators._check_policy_file(ws, path, name)
    assert findings == [], (
        f"filled-in starter {name} still fails validation: "
        f"{[(f.requirement_id, f.missing_evidence) for f in findings]}"
    )


def test_v4_testing_and_verification_own_portfolio_maintenance() -> None:
    testing = starters.read_starter("testing-policy.md")
    verification = starters.read_starter("verification-policy.md")

    for phrase in (
        "nearest existing coverage",
        "`REMOVE`, `MERGE`, `REPLACE`, or `KEEP`",
        "cheapest sufficient layer",
        "budget-neutral",
        "retention review",
    ):
        assert phrase in testing, phrase

    for phrase in (
        "default",
        "triggered",
        "human",
        "operational",
        "one-off",
        "owner",
    ):
        assert phrase in verification.lower(), phrase


def test_every_starter_delegates_shared_rules_to_the_portfolio() -> None:
    for name in sorted(starters.iter_starter_names()):
        content = starters.read_starter(name)
        assert "## Portfolio ownership" in content, name
        assert "policy-portfolio.toml" in content, name
        assert "domain-specific" in content, name


def test_remediation_prompt_owns_the_fill_in_instructions() -> None:
    prompt = remediation._render_prompt([])
    assert "Replace every `RALPH-FACT:` placeholder" in prompt
    assert "no unresolved or completion marker" in prompt.lower()
    assert "inspect the project's languages" in prompt
    assert "inactive conditional domain" in prompt
    assert "stricter" in prompt
    assert "RALPH-STARTER-TEMPLATE" in prompt


def test_clean_code_and_architecture_agree_on_abstraction_justification() -> None:
    clean_code = starters.read_starter("clean-code-policy.md")
    architecture = starters.read_starter("architecture-policy.md")
    cc_norm = " ".join(clean_code.split())
    arch_norm = " ".join(architecture.split())

    assert "implementation count alone neither requires nor forbids" in cc_norm
    assert "implementation count alone neither requires nor forbids" in arch_norm

    assert "demonstrated boundary, volatility, testing, or reuse value" in cc_norm
    assert "demonstrated boundary, volatility, testing, or reuse need" in arch_norm

    for text, name in (
        (clean_code, "clean-code-policy.md"),
        (architecture, "architecture-policy.md"),
    ):
        assert "second implementation" not in text.lower(), (
            f"{name} must not use a second-implementation proxy for abstraction"
        )
        assert "one-implementation abstraction is premature" not in text.lower(), (
            f"{name} must not declare a one-implementation abstraction inherently premature"
        )

    abstraction_rows = [
        line
        for line in clean_code.splitlines()
        if "abstraction" in line.lower() and line.strip().startswith("|")
    ]
    assert abstraction_rows, "clean-code guard inventory must have an abstraction guard row"
    for line in abstraction_rows:
        line_lower = line.lower()
        assert "second implementation" not in line_lower, (
            "clean-code abstraction guard row must not use a second-implementation proxy"
        )
        assert "demonstrated boundary, volatility, testing, or reuse" in line_lower, (
            "clean-code abstraction guard row must justify abstraction by demonstrated value"
        )
