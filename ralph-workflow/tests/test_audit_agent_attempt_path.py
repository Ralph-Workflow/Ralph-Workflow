from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing.audit_agent_attempt_path import (
    ARTIFACTLESS_ATTEMPT_RULE,
    RAW_AGENT_INVOKE_RULE,
    SELECTION_RULE,
    audit_modules,
    audit_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SUB_PIPELINE = "ralph/project_policy/_planted_sub_pipeline.py"


def _rules(source: str, rel_path: str = SUB_PIPELINE) -> list[str]:
    return [violation.rule for violation in audit_modules({rel_path: source})]


@pytest.mark.timeout_seconds(20)
def test_repository_tree_routes_every_agent_attempt_through_the_shared_path() -> None:
    assert audit_tree(REPO_ROOT) == []


def test_a_sub_pipeline_that_selects_and_waits_on_its_own_is_rejected() -> None:
    source = (
        "def pick(controller, agents):\n"
        "    selection = controller.preferred_agent_index('p', agents)\n"
        "    if selection.agent is None:\n"
        "        controller.clock.sleep(controller.earliest_available_wait_ms('p', agents))\n"
        "    return selection.agent\n"
    )
    assert _rules(source) == [SELECTION_RULE, SELECTION_RULE]


def test_a_raw_process_launch_outside_the_shared_path_is_rejected() -> None:
    assert _rules("from ralph.agents.invoke import invoke_agent\n") == [RAW_AGENT_INVOKE_RULE]
    assert _rules(
        "import ralph.agents.invoke as invoke_module\n"
        "def go(config, prompt):\n"
        "    return invoke_module.invoke_agent(config, prompt)\n"
    ) == [RAW_AGENT_INVOKE_RULE]


def test_an_artifactless_attempt_needs_a_progress_check() -> None:
    effect = "InvokeAgentEffect(agent_name=a, requires_completion_evidence=phase == 'analysis')"
    assert _rules(f"def build(a, phase):\n    return {effect}\n") == [ARTIFACTLESS_ATTEMPT_RULE]
    assert (
        _rules(
            "def run(phase):\n"
            f"    return run_chain_attempt(build_effect=lambda a: {effect},\n"
            "                             progress_check=lambda: True)\n"
        )
        == []
    )


def test_an_attempt_that_requires_an_artifact_needs_no_progress_check() -> None:
    assert (
        _rules(
            "def build(a):\n    return InvokeAgentEffect(agent_name=a, requires_completion_evidence=True)\n"
        )
        == []
    )
