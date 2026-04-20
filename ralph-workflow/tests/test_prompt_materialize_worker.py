from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import render_worker_prompt

if TYPE_CHECKING:
    from pathlib import Path


BASE_PROMPT = "Base context: only work on your assigned unit."


def test_render_worker_prompt_includes_unit_specific_description_and_base_prompt(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    unit = WorkUnit(
        unit_id="worker-1",
        description="Implement isolated worker prompt materialization",
        allowed_directories=["ralph/prompts", "tests"],
    )

    rendered = render_worker_prompt(unit=unit, base_prompt=BASE_PROMPT, policy=policy.pipeline)

    assert "worker-1" in rendered
    assert "Implement isolated worker prompt materialization" in rendered
    assert BASE_PROMPT in rendered


def test_render_worker_prompt_lists_allowed_directories_as_json(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    unit = WorkUnit(
        unit_id="worker-2",
        description="Touch prompt files only",
        allowed_directories=["ralph/prompts/templates", "ralph/prompts"],
    )

    rendered = render_worker_prompt(unit=unit, base_prompt=BASE_PROMPT, policy=policy.pipeline)

    assert json.dumps(unit.allowed_directories, indent=2) in rendered


def test_render_worker_prompt_does_not_leak_other_unit_data(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    first_unit = WorkUnit(
        unit_id="worker-alpha",
        description="Implement only alpha behavior",
        allowed_directories=["ralph/prompts"],
    )
    second_unit = WorkUnit(
        unit_id="worker-beta",
        description="Implement only beta behavior",
        allowed_directories=["ralph/pipeline"],
    )

    rendered = render_worker_prompt(
        unit=first_unit,
        base_prompt=BASE_PROMPT,
        policy=policy.pipeline,
    )

    assert first_unit.description in rendered
    assert second_unit.description not in rendered
    assert second_unit.unit_id not in rendered
    assert json.dumps(second_unit.allowed_directories, indent=2) not in rendered
