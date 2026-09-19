"""Regression coverage for drain-keyed validation retry context."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.mcp.artifacts.markdown import Diagnostic
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import (
    handle_edit_md_artifact,
    handle_finalize_md_artifact,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
)
from ralph.phases.required_artifacts import (
    build_validation_retry_hint,
    retry_hint_path,
)
from ralph.pipeline.effect_executor import (
    AgentRecoveryInput,
    _write_agent_retry_prompt,
    build_agent_recovery_plan,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from tests._tool_artifact_2_helper_memorybackend import MemoryBackend
from tests._tool_artifact_2_helper_mocksession import MockSession
from tests._tool_artifact_2_helper_mockworkspace import MockWorkspace

_INVALID_PRODUCT_SPEC = "---\ntype: product_spec\n---\n"
_VALID_PRODUCT_SPEC = """---
type: product_spec
---
## Title
- [T1] Retry context
## Scope
- [S1] Preserve validation failures
## Goals
- [G1] Repair the retained draft
## Users
- [U1] Agents
## Success Criteria
- [C1] Validator diagnostics are injected
"""


def _custom_pipeline_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development_commit_cleanup": PhaseDefinition(
                drain="commit",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="development_commit_cleanup",
        terminal_phase="complete",
    )


def test_retry_hint_path_resolves_policy_phase_to_its_drain() -> None:
    policy = _custom_pipeline_policy()

    assert retry_hint_path("development_commit_cleanup", pipeline_policy=policy) == (
        ".agent/tmp/last_retry_error_commit.txt"
    )
    assert retry_hint_path("commit", pipeline_policy=policy) == ".agent/tmp/last_retry_error_commit.txt"
    assert retry_hint_path("unknown", pipeline_policy=policy) == ".agent/tmp/last_retry_error_unknown.txt"
    assert retry_hint_path("custom_review_phase", pipeline_policy=object()) == (
        ".agent/tmp/last_retry_error_custom_review_phase.txt"
    )


def test_agent_retry_prompt_reads_drain_keyed_validator_context_without_consuming_it(
    tmp_path: Path,
) -> None:
    hint = "SPEC008 at line 7, section Goals: missing evidence"
    hint_path = tmp_path / retry_hint_path("commit")
    hint_path.parent.mkdir(parents=True)
    hint_path.write_text(hint, encoding="utf-8")
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Repair the commit cleanup artifact.", encoding="utf-8")

    retry_prompt = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_path),
        reason="ArtifactValidation",
        context_lines=["Prior work repaired cleanup decisions."],
        drain="commit",
    )

    content = Path(retry_prompt).read_text(encoding="utf-8")
    assert content.startswith("VALIDATION ERRORS (ACCUMULATED)")
    assert hint in content
    assert "retained draft" in content.lower()
    assert "Prior work repaired cleanup decisions." in content
    assert hint_path.read_text(encoding="utf-8") == hint


def test_validation_retry_history_keeps_all_headlines_and_escalates() -> None:
    hint = ""
    for attempt in range(1, 5):
        hint = build_validation_retry_hint(
            "product_spec",
            [Diagnostic(attempt, "Goals", f"SPEC00{attempt}", "failure")],
            prior_hint=hint,
        )

    for attempt in range(1, 5):
        assert f"ATTEMPT {attempt}" in hint
        assert f"SPEC00{attempt} at line {attempt}, section Goals: failure" in hint
    assert "THIS VALIDATION HAS FAILED 4 TIMES" in hint


def test_validation_retry_history_bounds_old_bodies_without_losing_headlines() -> None:
    hint = ""
    for attempt in range(1, 9):
        hint = build_validation_retry_hint(
            "product_spec",
            [Diagnostic(attempt, "Goals", f"SPEC{attempt:03}", "x" * 5000)],
            prior_hint=hint,
        )

    for attempt in range(1, 9):
        assert f"ATTEMPT {attempt}" in hint
        assert f"- SPEC{attempt:03} at line {attempt}, section Goals" in hint
    assert "[older attempt body truncated]" in hint


@pytest.mark.parametrize("operation", ["submit", "finalize", "edit"])
@pytest.mark.parametrize("worker", [False, True])
def test_successful_artifact_operations_clear_drain_and_legacy_phase_hints(
    tmp_path: Path,
    operation: str,
    worker: bool,
) -> None:
    session = MockSession(drain="commit")
    session.phase = "development_commit_cleanup"
    if worker:
        session.worker_namespace = tmp_path / ".agent" / "workers" / "unit-1"
    workspace = MockWorkspace(tmp_path)
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    hint_directory = session.worker_namespace / "tmp" if worker else tmp_path / ".agent" / "tmp"
    canonical_path = hint_directory / "last_retry_error_commit.txt"
    legacy_path = hint_directory / "last_retry_error_development_commit_cleanup.txt"
    backend.write_text(canonical_path, "canonical validator error")
    backend.write_text(legacy_path, "legacy validator error")

    if operation == "submit":
        result = handle_submit_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec", "content": _VALID_PRODUCT_SPEC},
            deps=deps,
        )
    elif operation == "finalize":
        handle_stage_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec", "content": _VALID_PRODUCT_SPEC, "mode": "replace_all"},
            deps=deps,
        )
        result = handle_finalize_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec"},
            deps=deps,
        )
    else:
        handle_stage_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec", "content": _INVALID_PRODUCT_SPEC, "mode": "replace_all"},
            deps=deps,
        )
        result = handle_edit_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec", "edits": [{"oldText": _INVALID_PRODUCT_SPEC, "newText": _VALID_PRODUCT_SPEC}]},
            deps=deps,
        )

    assert result.is_error is False
    assert not backend.exists(canonical_path)
    assert not backend.exists(legacy_path)


def test_recovery_prompt_reads_worker_validation_hint_without_coordinator_leak(tmp_path: Path) -> None:
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-1"
    worker_hint = worker_namespace / "tmp" / "last_retry_error_commit.txt"
    worker_hint.parent.mkdir(parents=True)
    worker_hint.write_text("SPEC777 at line 9, section Goals: worker failure", encoding="utf-8")
    coordinator_hint = tmp_path / ".agent" / "tmp" / "last_retry_error_commit.txt"
    coordinator_hint.parent.mkdir(parents=True)
    coordinator_hint.write_text("SPEC999 coordinator-only", encoding="utf-8")
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Repair the worker artifact.", encoding="utf-8")

    plan = build_agent_recovery_plan(
        AgentRecoveryInput(
            exc=AgentInactivityTimeoutError("agent", 1.0),
            attempt_index=0,
            max_recovery_attempts=1,
            effect=InvokeAgentEffect(
                agent_name="agent",
                phase="development_commit_cleanup",
                prompt_file=str(prompt_path),
                drain="commit",
            ),
            workspace_root=tmp_path,
            raw_output=[],
            rendered_output=[],
            extracted_session_id=None,
            inactivity_error_type=AgentInactivityTimeoutError,
            worker_namespace=worker_namespace,
        )
    )

    assert plan is not None
    retry_content = Path(plan.prompt_file).read_text(encoding="utf-8")
    assert "SPEC777 at line 9, section Goals: worker failure" in retry_content
    assert "SPEC999 coordinator-only" not in retry_content


def test_fresh_retry_prompt_without_validation_hint_starts_with_error_block(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Repair the artifact.", encoding="utf-8")

    retry_prompt = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_path),
        reason="ArtifactValidation",
        context_lines=[],
        drain="commit",
    )

    assert Path(retry_prompt).read_text(encoding="utf-8").startswith("ERROR RECOVERY REQUIRED")


def test_recovery_controller_writes_session_reset_hint_for_effective_drain(tmp_path: Path) -> None:
    bundle = load_policy(tmp_path / ".agent")
    controller = RecoveryController(options=RecoveryControllerOptions(policy_bundle=bundle))
    failure = ClassifiedFailure(
        category=FailureCategory.ENVIRONMENTAL,
        reason="stale session",
        attributed_agent=None,
        attributed_phase="development_commit_cleanup",
        counts_against_budget=False,
        original_exception=None,
        raw_message="stale session",
    )
    backend = MemoryBackend()

    controller._write_session_reset_hint("development_commit_cleanup", failure, backend=backend)

    assert backend.exists(Path(".agent/tmp/last_retry_error_commit.txt"))
    assert not backend.exists(Path(".agent/tmp/last_retry_error_development_commit_cleanup.txt"))


def test_failed_submit_writes_drain_keyed_hint_for_custom_phase(tmp_path: Path) -> None:
    session = MockSession(drain="commit")
    session.phase = "development_commit_cleanup"
    workspace = MockWorkspace(tmp_path)
    backend = MemoryBackend()

    result = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _INVALID_PRODUCT_SPEC},
        deps=ArtifactHandlerDeps(backend=backend),
    )

    assert result.is_error is True
    assert backend.exists(tmp_path / retry_hint_path("commit"))
    assert not backend.exists(tmp_path / retry_hint_path("development_commit_cleanup"))
