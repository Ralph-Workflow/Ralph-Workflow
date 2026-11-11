from __future__ import annotations

import re

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, preflight, starters, validators
from ralph.project_policy.models import ReadinessStatus
from ralph.workspace.memory import MemoryWorkspace

_FILENAME = "gate-script-policy.md"
_PATH = f"{markers.CANONICAL_DIR}{_FILENAME}"

_RESOLVED_FACTS = {
    "supported_platforms": "linux, macos",
    "shell_dialect": "bash",
    "script_directory": "scripts/",
}


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python", secondary_languages=[], frameworks=[])


def _complete_gate_script_policy() -> str:
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
    content = content.replace(
        "deterministic: PROJECT-FACT-UNRESOLVED",
        "deterministic: shellcheck scripts/",
    )
    return re.sub(
        r"<!--\s*(RALPH-STARTER-TEMPLATE|REPLACE-ME):.*?-->", "", content, flags=re.DOTALL
    )


def test_gate_script_policy_is_a_core_starter() -> None:
    assert _FILENAME in markers.CORE_POLICY_FILES
    assert _FILENAME in set(starters.iter_starter_names())


def test_starter_carries_every_required_heading() -> None:
    content = starters.read_starter(_FILENAME)
    for heading in markers.REQUIRED_HEADINGS[_FILENAME]:
        assert f"## {heading}" in content, heading


def test_security_and_cross_platform_are_required_headings() -> None:
    required = markers.REQUIRED_HEADINGS[_FILENAME]
    assert "Security" in required
    assert "Cross-platform" in required


def test_resolving_only_the_facts_and_command_reaches_ready() -> None:
    workspace = MemoryWorkspace()
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    workspace.write(_PATH, _complete_gate_script_policy())

    findings = validators.validate_readiness(workspace, _stack())

    gate_script_findings = [f for f in findings if f.path == _PATH]
    assert gate_script_findings == [], gate_script_findings


def test_unresolved_starter_blocks_readiness() -> None:
    workspace = MemoryWorkspace()
    assert starters.seed_starter_into(workspace, _FILENAME) is True

    findings = validators.validate_readiness(workspace, _stack())

    assert [f for f in findings if f.path == _PATH], (
        "an unresolved gate-script starter must block readiness"
    )


def test_preflight_seeds_the_gate_script_policy() -> None:
    workspace = MemoryWorkspace()

    result = preflight.run_policy_readiness_preflight(workspace, _stack())

    assert result.status is ReadinessStatus.REMEDIATION_REQUIRED
    assert _PATH in result.changed_files
    assert workspace.exists(_PATH)


@pytest.mark.parametrize(
    "requirement",
    [
        "fail **closed**",
        "set -euo pipefail",
        "No phantom dependencies",
        "FAILS when it should",
        "No hollow gates",
        "owns only its own correctness",
    ],
)
def test_normative_requirements_ship_complete(requirement: str) -> None:
    assert requirement in starters.read_starter(_FILENAME)


def test_windows_rules_are_conditional_on_the_supported_platforms_fact() -> None:
    content = starters.read_starter(_FILENAME)
    assert "If `supported_platforms` includes Windows" in content
    assert "does not include Windows" in content
    assert "RALPH-FACT: supported_platforms:" in content
