from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import phase_payload_variables, render_worker_prompt
from ralph.prompts.payload_refs import MAX_INLINE_PROMPT_BYTES

BASE_PROMPT = "Base context: only work on your assigned unit."
# Content large enough to trigger file-based payload routing (>100KB).
_LARGE_CONTENT = "x" * (MAX_INLINE_PROMPT_BYTES + 1)


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


def test_worker_namespace_routes_payloads(tmp_path: Path) -> None:
    """When worker_namespace is set, oversized payloads land in the namespaced dir."""
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
    workspace_root = tmp_path

    phase_payload_variables(
        phase="review",
        workspace_root=workspace_root,
        worker_namespace=worker_ns,
        values={"PLAN": _LARGE_CONTENT, "DIFF": "small diff"},
    )

    expected_dir = worker_ns / "tmp" / "prompt_payloads"
    shared_dir = workspace_root / ".agent" / "tmp" / "prompt_payloads"

    assert expected_dir.exists(), "Worker-namespaced payload dir must be created"
    assert not shared_dir.exists(), "Shared singleton payload dir must NOT be written"

    written_files = list(expected_dir.iterdir())
    assert len(written_files) > 0, "At least one payload file must be written to worker namespace"


def test_phase_payload_variables_without_namespace_uses_shared_path(tmp_path: Path) -> None:
    """Without worker_namespace, oversized payloads go to the shared singleton path."""
    workspace_root = tmp_path

    phase_payload_variables(
        phase="review",
        workspace_root=workspace_root,
        values={"PLAN": _LARGE_CONTENT, "DIFF": "small diff"},
    )

    shared_dir = workspace_root / ".agent" / "tmp" / "prompt_payloads"
    assert shared_dir.exists(), "Shared payload dir must be created when no namespace provided"


def test_two_concurrent_namespaces_dont_collide(tmp_path: Path) -> None:
    """Two workers with different namespaces must not write to each other's directories."""
    ns_a = tmp_path / ".agent" / "workers" / "unit-a"
    ns_b = tmp_path / ".agent" / "workers" / "unit-b"
    workspace_root = tmp_path

    phase_payload_variables(
        phase="review",
        workspace_root=workspace_root,
        worker_namespace=ns_a,
        values={"PLAN": _LARGE_CONTENT, "DIFF": "diff-a"},
    )
    phase_payload_variables(
        phase="review",
        workspace_root=workspace_root,
        worker_namespace=ns_b,
        values={"PLAN": _LARGE_CONTENT, "DIFF": "diff-b"},
    )

    dir_a = ns_a / "tmp" / "prompt_payloads"
    dir_b = ns_b / "tmp" / "prompt_payloads"
    shared_dir = workspace_root / ".agent" / "tmp" / "prompt_payloads"

    assert dir_a.exists()
    assert dir_b.exists()
    assert not shared_dir.exists(), "Shared path must not be written when namespaces are provided"

    files_a = {f.name for f in dir_a.iterdir()}
    files_b = {f.name for f in dir_b.iterdir()}
    assert files_a and files_b, "Both namespaces must have payload files"
