"""Runtime extraction keeps arbitrary plans safe without judging their shape."""

import json
from pathlib import Path

from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.pipeline.work_units import canonical_plan_references, parse_work_units_from_artifact
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope


def test_worker_ownership_drops_protected_paths_and_infers_step_files() -> None:
    plan = {
        "steps": [{"id": "S-1", "files": ["src/one.py", ".git/config"]}],
        "work_units": [
            {"unit_id": "U-1", "description": "Implement one", "step_ids": ["S-1"]},
            {
                "unit_id": "U-2",
                "description": "Implement two",
                "directories": ["./.agent/tmp", "src"],
                "paths": [".worktrees/a", "tests/t.py"],
            },
        ],
    }
    parsed = parse_work_units_from_artifact(plan)
    assert parsed is not None
    assert parsed.work_units[0].paths == ["src/one.py"]
    assert parsed.work_units[1].allowed_directories == ["src"]
    assert parsed.work_units[1].paths == ["tests/t.py"]


def test_proof_preserves_unit_ids_independently_of_description_length() -> None:
    content = {
        "steps": [{"id": "S-1"}, {"id": "invalid"}, {"id": "S-1"}],
        "work_units": [
            {
                "unit_id": "U-1",
                "description": "Detailed implementation " * 300,
                "step_ids": ["S-1", "S-999", "broken", "S-1"],
            }
        ],
    }
    assert canonical_plan_references(content) == (
        frozenset({"S-1"}),
        frozenset({"U-1"}),
        frozenset({"S-1"}),
    )


def test_mixed_markdown_units_keep_paths_and_inferred_files() -> None:
    content, diagnostics, _ = (
        analyze_plan_document("""Implement the independent components and verify their integration together after completion.

## Work Units
- [explicit] Implement first component
  Paths: src/first.py
- [inferred] Implement second component

### [S-1] Change second component
Files:
- modify src/second.py

## Parallel Plan
- [legacy] Implement third component
  Paths: src/third.py
""")
    )
    assert diagnostics == []
    parsed = parse_work_units_from_artifact(content)
    assert parsed is not None
    assert {unit.unit_id: unit.paths for unit in parsed.work_units} == {
        "explicit": ["src/first.py"],
        "inferred": ["src/second.py"],
        "legacy": ["src/third.py"],
    }


def test_worker_prompt_carries_exact_file_ownership() -> None:
    workspace = MemoryWorkspace()
    prompt = prompt_developer_iteration_xml_with_context(
        TemplateContext.default(),
        DeveloperPromptInputs(
            prompt_content="Implement requested change",
            plan_content="Implement the file and demonstrate its observable correctness.",
            work_unit_id="one",
            work_unit_paths='["src/one.py"]',
        ),
        workspace,
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name="worker_developer.jinja",
    )
    assert "src/one.py" in prompt


def test_worker_file_scope_never_includes_the_parent_directory() -> None:
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=Path("/workspace"),
        allowed_directories=(),
        paths=("src/one.py",),
        worker_namespace=Path("/workspace/.agent/workers/one"),
    )
    assert Path("/workspace/src/one.py") in scope.allowed_roots
    assert Path("/workspace/src") not in scope.allowed_roots
    assert Path("/workspace") not in scope.allowed_roots


def test_rendered_developer_guidance_uses_unit_plus_unowned_step_proof() -> None:
    prompt = prompt_developer_iteration_xml_with_context(
        TemplateContext.default(),
        DeveloperPromptInputs(
            prompt_content="Implement the requested behavior",
            plan_content="Implement independent units and integrate their results with focused verification.",
        ),
        MemoryWorkspace(),
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
    )
    # Free-form contract: the structured ``Unit proof covers its owned
    # steps`` / ``every unowned global step`` proof-language references
    # are removed. Coverage is judged in plain language against the
    # plan, not by matching proof-section IDs.
    assert "Unit proof covers its owned steps" not in prompt
    assert "every unowned global step" not in prompt
    # The remaining unit-handling guidance still names the
    # independent-units fan-out and per-unit verification.
    assert "independent units" in prompt.lower() or "work unit" in prompt.lower()


def test_protected_ownership_is_absent_from_rendered_worker_scope() -> None:
    parsed = parse_work_units_from_artifact(
        {
            "work_units": [
                {
                    "unit_id": "one",
                    "directories": [".agent/secret", ".git/hooks", "src"],
                    "paths": [".worktrees/secret.py", "tests/one.py"],
                }
            ],
        }
    )
    assert parsed is not None
    unit = parsed.work_units[0]
    prompt = prompt_developer_iteration_xml_with_context(
        TemplateContext.default(),
        DeveloperPromptInputs(
            prompt_content="Implement the requested behavior",
            plan_content="Implement independent units and integrate their results with focused verification.",
            work_unit_id=unit.unit_id,
            work_unit_directories=json.dumps(unit.allowed_directories),
            work_unit_paths=json.dumps(unit.paths),
        ),
        MemoryWorkspace(),
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name="worker_developer.jinja",
    )
    assert '["src"]' in prompt
    assert '["tests/one.py"]' in prompt
    for forbidden in (".agent/secret", ".git/hooks", ".worktrees/secret.py"):
        assert forbidden not in prompt


def test_mixed_unit_sections_preserve_cross_section_step_dependencies() -> None:
    content, diagnostics, _ = analyze_plan_document("""## Work Units
- [producer] Implement shared producer
  Paths: src/producer.py

### [S-1] Build producer
Files:
- modify src/producer.py

## Parallel Plan
- [consumer] Implement independent consumer
  Paths: src/consumer.py

### [S-2] Build consumer
Depends on: S-1
Files:
- modify src/consumer.py
""")
    assert diagnostics == []
    parsed = parse_work_units_from_artifact(content)
    assert parsed is not None
    assert {unit.unit_id: unit.dependencies for unit in parsed.work_units} == {
        "producer": [],
        "consumer": ["producer"],
    }
