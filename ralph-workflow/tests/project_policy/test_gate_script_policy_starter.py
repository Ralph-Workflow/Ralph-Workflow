"""The gate-script policy is a core starter the remediator OBEYS, not rewrites.

Every other starter is a form: the remediation agent rewrites its body with
verified project facts. This one is different. Its requirement sections ship as
NORMATIVE STANDARD TEXT — the agent reads them and conforms its gate scripts to
them, resolving only three facts and one command. These tests pin both halves of
that contract: the standard text must be present and load-bearing, and the small
resolvable part must actually be resolvable to a READY state.
"""

from __future__ import annotations

import re

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, preflight, starters, validators
from ralph.project_policy.models import ReadinessStatus
from ralph.workspace.memory import MemoryWorkspace

_FILENAME = "gate-script-policy.md"
_PATH = f"{markers.CANONICAL_DIR}{_FILENAME}"

# The three facts the remediation agent must resolve, and a plausible resolved
# value for each. Anything the agent may NOT invent (a shell dialect the project
# does not use, a script dir that does not exist) is out of scope here — this
# fixture only proves the starter is completable.
_RESOLVED_FACTS = {
    "supported_platforms": "linux, macos",
    "shell_dialect": "bash",
    "script_directory": "scripts/",
}


def _stack() -> ProjectStack:
    return ProjectStack(
        primary_language="Python", secondary_languages=[], frameworks=[]
    )


def _complete_gate_script_policy() -> str:
    """Return the starter as a remediation agent would leave it.

    Performs exactly the three operations the starter's banner instructs:
    resolve each fact, resolve the one command, and delete the banner and every
    REPLACE-ME comment. Nothing else is touched — which is the point.
    """
    content = starters.read_starter(_FILENAME)
    for key, value in _RESOLVED_FACTS.items():
        content = content.replace(
            f"RALPH-FACT: {key}: PROJECT-FACT-UNRESOLVED",
            f"RALPH-FACT: {key}: {value}",
        )
    content = content.replace(
        "RALPH-COMMAND: PROJECT-FACT-UNRESOLVED",
        "RALPH-COMMAND: shellcheck scripts/",
    )
    # Strip the starter banner and every REPLACE-ME guidance comment.
    return re.sub(r"<!--\s*(RALPH-STARTER-TEMPLATE|REPLACE-ME):.*?-->", "", content, flags=re.DOTALL)


def test_gate_script_policy_is_a_core_starter() -> None:
    assert _FILENAME in markers.CORE_POLICY_FILES
    assert _FILENAME in set(starters.iter_starter_names())


def test_starter_carries_every_required_heading() -> None:
    content = starters.read_starter(_FILENAME)
    for heading in markers.REQUIRED_HEADINGS[_FILENAME]:
        assert f"## {heading}" in content, heading


def test_security_and_cross_platform_are_required_headings() -> None:
    """A gate script is an execution surface and a portability surface. Neither
    section may be dropped by an amendment, so both are structurally required."""
    required = markers.REQUIRED_HEADINGS[_FILENAME]
    assert "Security" in required
    assert "Cross-platform" in required


def test_resolving_only_the_facts_and_command_reaches_ready() -> None:
    """The starter must be completable by resolving its three facts and one
    command — without rewriting a word of the normative requirements."""
    workspace = MemoryWorkspace()
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    workspace.write(_PATH, _complete_gate_script_policy())

    findings = validators.validate_readiness(workspace, _stack())

    gate_script_findings = [f for f in findings if f.path == _PATH]
    assert gate_script_findings == [], gate_script_findings


def test_unresolved_starter_blocks_readiness() -> None:
    """A freshly seeded starter must NOT validate: the banner and the three
    unresolved facts keep the project out of READY until the agent acts."""
    workspace = MemoryWorkspace()
    assert starters.seed_starter_into(workspace, _FILENAME) is True

    findings = validators.validate_readiness(workspace, _stack())

    assert [f for f in findings if f.path == _PATH], (
        "an unresolved gate-script starter must block readiness"
    )


def test_preflight_seeds_the_gate_script_policy() -> None:
    """The 11th core policy reaches a fresh project through the normal seeding
    path, so agents can read it without any extra wiring."""
    workspace = MemoryWorkspace()

    result = preflight.run_policy_readiness_preflight(workspace, _stack())

    assert result.status is ReadinessStatus.REMEDIATION_REQUIRED
    assert _PATH in result.changed_files
    assert workspace.exists(_PATH)


@pytest.mark.parametrize(
    "requirement",
    [
        # The exit-code contract and fail-closed rule.
        "fail **closed**",
        # Strict mode, named concretely so it is actionable.
        "set -euo pipefail",
        # The anti-fabrication rule — the highest-signal failure mode of a
        # documentation agent writing scripts.
        "No phantom dependencies",
        # Scripts are tested code, INCLUDING their failure path.
        "FAILS when it should",
        # The hollow-gate ban.
        "No hollow gates",
        # The fault-attribution rule: a script that reports a real failure is a
        # WORKING script. Without this, the remediator "fixes" red gates.
        "owns only its own correctness",
    ],
)
def test_normative_requirements_ship_complete(requirement: str) -> None:
    """These requirements are the reason the file exists. They ship resolved —
    no placeholder, no REPLACE-ME — because the agent obeys them rather than
    authoring them."""
    assert requirement in starters.read_starter(_FILENAME)


def test_windows_rules_are_conditional_on_the_supported_platforms_fact() -> None:
    """Portability binds only when the project actually targets Windows. The
    starter must say so, and must tie it to the resolved fact rather than to an
    assumption."""
    content = starters.read_starter(_FILENAME)
    assert "If `supported_platforms` includes Windows" in content
    assert "does not include Windows" in content
    assert "RALPH-FACT: supported_platforms:" in content
