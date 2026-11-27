from __future__ import annotations

from ralph.project_policy.starters import read_starter
from ralph.visual.policy_facts import parse_policy_facts


def _filled_design_policy() -> str:
    return (
        read_starter("design-system-policy.md")
        .replace("PROJECT-FACT-UNRESOLVED", "http://127.0.0.1:3000/")
        .replace(
            "<declared capture command that renders the managed repo UI to PNG>",
            "npx playwright screenshot",
        )
    )


def test_the_design_starter_declares_every_fact_the_visual_pipeline_requires() -> None:
    facts = parse_policy_facts(_filled_design_policy())

    assert facts.target == "http://127.0.0.1:3000/"
    assert [(v.width, v.height) for v in facts.viewports] == [(375, 812), (1440, 900)]
