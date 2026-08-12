from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.prompts.materialize as materialize_module

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.prompts._multimodal_sidecar_entry import MultimodalSidecarEntry
from ralph.prompts.materialize import (
    materialize_prompt_for_phase,
    phase_payload_variables,
)
from ralph.prompts.payload_refs import MAX_INLINE_PROMPT_BYTES
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

# Content large enough to trigger file-based payload routing (>100KB).
_LARGE_CONTENT = "x" * (MAX_INLINE_PROMPT_BYTES + 1)


def test_materialized_worker_prompt_has_one_unit_scoped_contract(
    tmp_path: Path,
) -> None:
    workspace = MemoryWorkspace(root=tmp_path)
    workspace.write("PROMPT.md", "Implement the requested behavior.")
    workspace.write(
        ".agent/artifacts/plan.md",
        """---
type: plan
---

## Summary
Implement the behavior.

## Steps
- [ ] [S-1] Implement it.
""",
    )
    policy = load_policy(tmp_path / ".agent")
    unit = WorkUnit(
        unit_id="api",
        description="Implement the API",
        allowed_directories=["src/api"],
        step_ids=["S-1"],
    )

    path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        artifacts_policy=policy.artifacts,
        work_unit=unit,
    )
    rendered = workspace.read(path)

    assert rendered.count("## WORKER SCOPE") == 1
    assert sum(line.strip() == "## Plan Items Proven" for line in rendered.splitlines()) == 1
    assert "responsible for dispatching your own sub-agents" not in rendered
    assert "integrate the combined result" not in rendered
    assert "- [S-1]" not in rendered
    assert "- [api]" in rendered
    assert "assigned unit as your sole required plan reference" in rendered
    assert "return that one unit result" in rendered
    assert "advance to the next ready reference" not in rendered
    worker_namespace = tmp_path / ".agent" / "workers" / "api"
    assert str(worker_namespace / "artifacts" / "development_result.md") in rendered
    assert str(worker_namespace / "handoffs" / "DEVELOPMENT_RESULT.md") in rendered
    assert "`.agent/tmp/development_result.md`" not in rendered
    assert "A `status: partial` or `status: failed` result is free-form" in rendered
    assert "`## Next Steps`" in rendered
    assert "`## Continuation`" in rendered
    assert "Do not invent files or verification results." in rendered
    normalized = " ".join(rendered.split())
    assert "the receipt is not phase completion" in normalized
    assert "MANDATORY FINAL ACTION" in rendered
    receipt_index = normalized.index("promote that worker-local fallback")
    completion_index = normalized.index("Only after that receipt exists")
    assert receipt_index < completion_index
    assert "call `declare_complete` as the final action" in normalized[completion_index:]
    assert "Do not call completion for an unvalidated fallback." not in rendered


def test_worker_analysis_loopback_keeps_worker_scope_and_continuation_gate(
    tmp_path: Path,
) -> None:
    workspace = MemoryWorkspace(root=tmp_path)
    workspace.write("PROMPT.md", "Implement the requested behavior.")
    workspace.write(
        ".agent/artifacts/plan.md",
        """---
type: plan
---

## Backend Subplan

### [S-1] Implement it
Implement the backend behavior.

Type: action
""",
    )
    analysis_feedback = """---
type: development_analysis_decision
status: request_changes
---

## Summary
- [SUM-1] The worker result needs another pass.

## What Came Up Short
- [W-1] Missing focused verification.

## How To Fix
- [W-1] Run the focused worker test.
"""
    workspace.write(
        ".agent/artifacts/development_analysis_decision.md",
        analysis_feedback,
    )
    workspace.write(".agent/DEVELOPMENT_ANALYSIS_DECISION.md", analysis_feedback)
    policy = load_policy(tmp_path / ".agent")
    unit = WorkUnit(
        unit_id="api",
        description="Implement the API",
        allowed_directories=["src/api"],
        step_ids=["S-1"],
    )

    path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        artifacts_policy=policy.artifacts,
        worker_namespace=tmp_path / ".agent" / "workers" / unit.unit_id,
        work_unit=unit,
        previous_phase="development_analysis",
    )
    rendered = workspace.read(path)

    assert "## WORKER SCOPE" in rendered
    assert "**Unit ID**: api" in rendered
    assert str(tmp_path / ".agent" / "DEVELOPMENT_ANALYSIS_DECISION.md") in rendered
    assert "when coordination costs less than sequential execution" in rendered
    assert "you MUST NOT submit the artifact or declare completion" in rendered


def test_worker_partial_result_takes_precedence_over_shared_continuation_context(
    tmp_path: Path,
) -> None:
    workspace = MemoryWorkspace(root=tmp_path)
    workspace.write("PROMPT.md", "Implement the requested behavior.")
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\n---\n## API Subplan\n"
        "### [S-1] Implement API\nImplement it.\n\nType: action\n",
    )
    worker_namespace = tmp_path / ".agent" / "workers" / "api"
    worker_partial = """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Worker-local implementation is incomplete.
## Files Changed
- [FC-1] src/api/main.py
## Next Steps
- [NEXT-1] Finish the worker-local API test.
## Continuation
- [CONT-1] worker-session-7
"""
    shared_partial = worker_partial.replace(
        "Worker-local implementation is incomplete.",
        "WRONG SHARED CONTEXT",
    )
    workspace.write(
        str(worker_namespace / "artifacts" / "development_result.md"),
        worker_partial,
    )
    workspace.write(".agent/artifacts/development_result.md", shared_partial)
    policy = load_policy(tmp_path / ".agent")
    unit = WorkUnit(
        unit_id="api",
        description="Implement the API",
        allowed_directories=["src/api"],
        step_ids=["S-1"],
    )

    path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        artifacts_policy=policy.artifacts,
        worker_namespace=worker_namespace,
        work_unit=unit,
    )
    rendered = workspace.read(path)

    assert "PRIOR WORKER RESULT — PARTIAL — NOT COMPLETE" in rendered
    assert "Worker-local implementation is incomplete." in rendered
    assert "Finish the worker-local API test." in rendered
    assert "worker-session-7" in rendered
    assert "WRONG SHARED CONTEXT" not in rendered
    assert "when coordination costs less than sequential execution" in rendered


def test_worker_materialization_preserves_shared_development_history(
    tmp_path: Path,
) -> None:
    workspace = MemoryWorkspace(root=tmp_path)
    workspace.write("PROMPT.md", "Implement the requested behavior.")
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\n---\n### [S-1] Implement API\nImplement it.\n\nType: action\n",
    )
    shared_history = (
        tmp_path / ".agent" / "artifacts" / "history" / "development_result" / "index.md"
    )
    shared_history.parent.mkdir(parents=True)
    shared_history.write_text("# Coordinator history\n", encoding="utf-8")
    policy = load_policy(tmp_path / ".agent")
    unit = WorkUnit(
        unit_id="api",
        description="Implement the API",
        allowed_directories=["src/api"],
        step_ids=["S-1"],
    )

    path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        artifacts_policy=policy.artifacts,
        worker_namespace=tmp_path / ".agent" / "workers" / unit.unit_id,
        work_unit=unit,
    )
    rendered = workspace.read(path)

    assert shared_history.read_text(encoding="utf-8") == "# Coordinator history\n"
    assert str(shared_history) not in rendered


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


def test_materialize_prompt_for_worker_runtime_uses_unit_specific_prompt_payload(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Base development prompt")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Implement the assigned unit\n")
    unit = WorkUnit(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
    )
    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        worker_namespace=tmp_path / ".agent" / "workers" / unit.unit_id,
        work_unit=unit,
    )

    rendered = workspace.read(prompt_path)

    assert "## WORKER SCOPE" in rendered
    assert "Implement only unit A" in rendered
    assert '[\n  "src/a"\n]' in rendered
    assert "responsible for dispatching your own sub-agents" not in rendered


def test_materialize_prompt_for_worker_runtime_does_not_wrap_non_development_prompt(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Base planning prompt")
    workspace.write(".agent/PLAN.md", "# Existing Plan\n")
    unit = WorkUnit(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
    )
    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        artifacts_policy=policy.artifacts,
        worker_namespace=tmp_path / ".agent" / "workers" / unit.unit_id,
        work_unit=unit,
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING MODE" in rendered
    assert "## WORKER SCOPE" not in rendered
    assert unit.description not in rendered


def test_persist_product_criteria_uses_worker_namespace_when_provided(
    tmp_path: Path,
) -> None:
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"

    product_criteria_path = materialize_module._persist_product_criteria(
        tmp_path,
        "Plan only the assigned worker task",
        worker_namespace=worker_namespace,
    )

    assert product_criteria_path == str(worker_namespace / "tmp" / "PRODUCT_CRITERIA.md")
    assert (worker_namespace / "tmp" / "PRODUCT_CRITERIA.md").read_text(encoding="utf-8") == (
        "Plan only the assigned worker task"
    )
    assert not (tmp_path / ".agent" / "PRODUCT_CRITERIA.md").exists()


def test_materialize_prompt_for_worker_runtime_writes_namespaced_prompt_and_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"

    monkeypatch.setattr(
        materialize_module,
        "_render_prompt_for_phase",
        lambda *_args, **_kwargs: "worker-scoped prompt body",
    )

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        worker_namespace=worker_namespace,
        multimodal_entries=[
            MultimodalSidecarEntry(
                artifact_id="artifact-1",
                uri="ralph://media/artifact-1",
                mime_type="image/png",
                title="diagram",
                modality="image",
                delivery="resource_reference_replay",
            )
        ],
    )

    assert prompt_path == str(worker_namespace / "tmp" / "planning_prompt.md")
    assert workspace.read(prompt_path) == "worker-scoped prompt body"
    assert workspace.read(str(worker_namespace / "tmp" / "planning_multimodal_handoff.json"))
    assert not workspace.exists(".agent/tmp/planning_prompt.md")
    assert not workspace.exists(".agent/tmp/planning_multimodal_handoff.json")
