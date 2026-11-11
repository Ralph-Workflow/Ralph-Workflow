from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.language_detector.models import ProjectStack
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.project_policy import markers, starters
from ralph.project_policy.models import PolicyFinding, ReadinessResult, ReadinessStatus

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

_LANG_POLICIES = frozenset({"typechecking-policy.md", "linting-policy.md"})


def stack() -> ProjectStack:
    return ProjectStack(primary_language="Python", secondary_languages=[], frameworks=[])


def complete_policy_body(filename: str) -> str:
    facts = [
        line.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
        for line in starters.read_starter(filename).splitlines()
        if line.startswith(markers.FACT_MARKER)
    ]
    headings = markers.REQUIRED_HEADINGS[filename]
    lines = [
        markers.POLICY_SCHEMA_MARKER,
        f"{markers.POLICY_ID_PREFIX} {filename} -->",
        "",
        "# Title",
        "",
    ]
    for heading in headings:
        lines.append(f"## {heading}")
        if heading == "Research basis":
            lines.extend(
                (
                    "",
                    "- publisher: Test Publisher",
                    "  title: Test Title",
                    "  http: https://example.com",
                    "  review date: 2026-07-13",
                    "",
                )
            )
        elif heading == "Bypass detection":
            lines.extend(("Real content.", "RALPH-COMMAND: make bypass-audit", ""))
        elif heading == markers.GUARD_INVENTORY_HEADING.removeprefix("## "):
            lines.extend(
                (
                    "",
                    "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |",
                    "|---|---|---|---|---|---|---|",
                    f"| test-guard-{filename} | test owner | flags missing test evidence | missing RALPH-FACT line is recorded as a finding | the count of missing RALPH-FACT lines per file | deterministic: make test | when the test policy is removed |",
                    "",
                )
            )
        else:
            lines.extend(("Real content.", ""))
    lines.extend(facts)
    lines.append(f"RALPH-FACT: {filename}: path = {markers.CANONICAL_DIR}{filename}")
    if filename in _LANG_POLICIES:
        lines.append("RALPH-LANG: Python")
    lines.append("RALPH-COMMAND: make test")
    return "\n".join(lines)


def policy_invocation_bundle() -> PolicyBundle:
    phase_names = (
        "planning",
        "planning_analysis",
        "development",
        "development_analysis",
        "development_commit",
    )
    chains = {name: AgentChainConfig(agents=["test-agent"]) for name in phase_names}
    chains["policy_remediation"] = AgentChainConfig(agents=["test-agent"])
    drains = {name: AgentDrainConfig(chain=name) for name in chains}
    phases = {
        name: PhaseDefinition(
            drain=name,
            transitions=PhaseTransition(on_success="complete"),
        )
        for name in phase_names
    }
    return PolicyBundle(
        agents=AgentsPolicy(agent_chains=chains, agent_drains=drains),
        pipeline=PipelinePolicy(phases=phases),
        artifacts=ArtifactsPolicy(),
    )


def remediation_required_result() -> ReadinessResult:
    return ReadinessResult(
        status=ReadinessStatus.REMEDIATION_REQUIRED,
        findings=[
            PolicyFinding(
                requirement_id="RWP-TEST:policy-boundary",
                path="AGENTS.md",
                missing_evidence="test policy boundary is not ready",
                required_outcome="invoke the remediation agent",
            )
        ],
    )


def seed_complete_corpus(workspace: Workspace, *, portfolio: str | None = None) -> None:
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    portfolio_content = portfolio or starters.read_starter(starters.PORTFOLIO_STARTER_NAME).replace(
        "PROJECT-FACT-UNRESOLVED", "make test"
    )
    workspace.write(
        markers.PORTFOLIO_PATH,
        portfolio_content,
    )
    for filename in markers.CORE_POLICY_FILES:
        workspace.write(f"{markers.CANONICAL_DIR}{filename}", complete_policy_body(filename))
    workspace.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    workspace.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )


__all__ = [
    "complete_policy_body",
    "policy_invocation_bundle",
    "remediation_required_result",
    "seed_complete_corpus",
    "stack",
]
