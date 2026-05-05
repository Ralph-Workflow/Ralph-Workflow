"""Tests: missing-artifact retry contract — hint written on failure, consumed on retry.

Covers the contract:
1. When a phase handler cannot find its required artifact, it writes a retry
   hint to .agent/tmp/last_retry_error_{phase}.txt
2. When materialize_prompt is called for the same phase, it reads the hint,
   surfaces it as LAST_RETRY_ERROR, and deletes the file so it doesn't leak
3. Parameterized over phases with declared artifact contracts in the default policy
4. The development phase writes a "missing input" hint when the plan is absent
   (plan is a planning-phase output, not a development submission), and a
   "missing output" hint when development_result is absent.
5. End-to-end retry flow: missing artifact → hint written → prompt includes
   LAST_RETRY_ERROR → second attempt with valid artifact → phase advances.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import ralph.prompts.materialize as materialize_module
from ralph.phases import PhaseContext
from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.phases.execution import handle_execution_phase
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    build_retry_hint,
    retry_hint_path,
)
from ralph.phases.review import handle_review
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import AnalysisDecisionEvent, PhaseFailureEvent, PipelineEvent
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import _read_and_clear_retry_hint
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


def _load_default_artifact_registry() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        policy = load_policy(Path(tmp) / ".agent")
        return build_required_artifacts(policy.artifacts)


REQUIRED_ARTIFACTS = _load_default_artifact_registry()

def _analysis_handler_for(phase_name: str):
    """Return a wrapper around handle_generic_analysis_phase with phase/drain pre-set."""
    def _handler(effect, ctx):
        real_effect = InvokeAgentEffect(agent_name="test", phase=phase_name, prompt_file="test.txt")
        return handle_generic_analysis_phase(real_effect, ctx)
    return _handler


def _execution_handler_for(phase_name: str):
    """Return a wrapper around handle_execution_phase with the correct phase set."""
    def _handler(effect, ctx):
        real_effect = InvokeAgentEffect(agent_name="test", phase=phase_name, prompt_file="test.txt")
        return handle_execution_phase(real_effect, ctx)
    return _handler


def _review_handler_for(phase_name: str):
    """Return a wrapper around handle_review with the correct phase set."""
    def _handler(effect, ctx):
        real_effect = InvokeAgentEffect(agent_name="test", phase=phase_name, prompt_file="test.txt")
        return handle_review(real_effect, ctx)
    return _handler


_PHASE_TO_HANDLER = {
    "planning": _execution_handler_for("planning"),
    "development": _execution_handler_for("development"),
    "development_analysis": _analysis_handler_for("development_analysis"),
    "review": _review_handler_for("review"),
    "review_analysis": _analysis_handler_for("review_analysis"),
}

# Legacy plan format: no "summary" key → _is_legacy_work_units_payload returns True,
# skipping full PlanArtifact pydantic validation in the development handler.
_VALID_PLAN_JSON_LEGACY = json.dumps({
    "work_units": [
        {"unit_id": "u1", "description": "do stuff", "allowed_directories": ["src"]}
    ]
})

# Full PlanArtifact-compliant JSON for use in tests that also call materialize_prompt,
# which triggers plan markdown handoff rendering via normalize_plan_artifact_content.
_VALID_PLAN_JSON_FULL = json.dumps({
    "type": "plan",
    "content": {
        "summary": {
            "context": "test context",
            "scope_items": [
                {"text": "item one"},
                {"text": "item two"},
                {"text": "item three"},
            ],
        },
        "steps": [
            {"number": 1, "title": "test step", "content": "do something"},
        ],
        "critical_files": {
            "primary_files": [{"path": "src/a.py", "action": "modify"}],
        },
        "risks_mitigations": [
            {"risk": "test risk", "mitigation": "test mitigation"},
        ],
        "verification_strategy": [
            {"method": "run tests", "expected_outcome": "tests pass"},
        ],
        "work_units": [
            {"unit_id": "u1", "description": "do stuff", "allowed_directories": ["src"]},
        ],
    },
})

_VALID_DEV_RESULT_JSON = json.dumps({
    "type": "development_result",
    "content": {
        "status": "completed",
        "summary": "Done.",
        "files_changed": "- src/a.py",
    },
})

_VALID_ISSUES_JSON = json.dumps({
    "type": "issues",
    "content": {
        "status": "no_issues",
        "summary": "Everything looks good.",
        "issues": [],
        "what_came_up_short": [],
        "how_to_fix": [],
    },
})

_VALID_FIX_RESULT_JSON = json.dumps({
    "type": "fix_result",
    "content": {
        "status": "completed",
        "summary": "Fixed.",
        "files_changed": "- src/a.py",
    },
})

_VALID_DEV_ANALYSIS_JSON = json.dumps({
    "type": "development_analysis_decision",
    "content": {"status": "completed"},
})

_VALID_REVIEW_ANALYSIS_JSON = json.dumps({
    "type": "review_analysis_decision",
    "content": {"status": "completed"},
})

_PHASE_VALID_ARTIFACT: dict[str, str] = {
    "planning": json.dumps({"type": "plan", "content": {"summary": "x"}}),
    "development": _VALID_DEV_RESULT_JSON,
    "development_analysis": _VALID_DEV_ANALYSIS_JSON,
    "review": _VALID_ISSUES_JSON,
    "review_analysis": _VALID_REVIEW_ANALYSIS_JSON,
    "fix": _VALID_FIX_RESULT_JSON,
}


def _make_ctx(workspace: MemoryWorkspace, policy=None) -> PhaseContext:
    if policy is None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = load_policy(Path(tmp) / ".agent")
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        agents_policy=object(),
    )


def _invoke_effect(phase: str = "unknown") -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="test", phase=phase, prompt_file="test.txt")


def _setup_phase_prerequisites(
    workspace: MemoryWorkspace, phase: str, *, full_plan: bool = False
) -> None:
    """Write required input artifacts for a phase so we test the *output* contract."""
    plan_json = _VALID_PLAN_JSON_FULL if full_plan else _VALID_PLAN_JSON_LEGACY
    if phase in {"development", "development_analysis", "review", "review_analysis", "fix"}:
        workspace.write(".agent/artifacts/plan.json", plan_json)
    if phase == "development_analysis":
        workspace.write(".agent/artifacts/development_result.json", _VALID_DEV_RESULT_JSON)
    elif phase in {"review_analysis", "fix"}:
        workspace.write(".agent/artifacts/issues.json", _VALID_ISSUES_JSON)


_OPTIONAL_ARTIFACTS = {
    p for p, ra in REQUIRED_ARTIFACTS.items() if not ra.artifact_required
}


@pytest.mark.parametrize(
    "phase",
    [
        p
        for p in REQUIRED_ARTIFACTS
        if p != "planning" and p in _PHASE_TO_HANDLER and p not in _OPTIONAL_ARTIFACTS
    ],
)
def test_missing_artifact_writes_retry_hint(phase: str) -> None:
    workspace = MemoryWorkspace()
    _setup_phase_prerequisites(workspace, phase)
    ctx = _make_ctx(workspace)

    handler = _PHASE_TO_HANDLER[phase]
    events = handler(_invoke_effect(), ctx)

    failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
    assert failure_events, f"Expected PhaseFailureEvent from {phase} when artifact is missing"
    assert failure_events[0].recoverable is True

    hint_path = retry_hint_path(phase)
    assert workspace.exists(hint_path), (
        f"Retry hint was not written to {hint_path} after {phase} failed to find artifact"
    )
    hint_content = workspace.read(hint_path)
    assert len(hint_content) > 0, "Retry hint must not be empty"


def test_planning_missing_plan_artifact_writes_retry_hint() -> None:
    workspace = MemoryWorkspace()
    ctx = _make_ctx(workspace)

    events = _execution_handler_for("planning")(_invoke_effect("planning"), ctx)

    failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
    assert failure_events, "Expected PhaseFailureEvent from planning when plan artifact is missing"
    assert failure_events[0].recoverable is True

    hint_path = retry_hint_path("planning")
    assert workspace.exists(hint_path)


def test_development_missing_plan_artifact_writes_retry_hint() -> None:
    workspace = MemoryWorkspace()
    ctx = _make_ctx(workspace)

    events = _execution_handler_for("development")(_invoke_effect("development"), ctx)

    failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
    assert failure_events
    assert failure_events[0].recoverable is True

    hint_path = retry_hint_path("development")
    assert workspace.exists(hint_path)


def test_development_missing_plan_hint_names_upstream_planning_phase() -> None:
    """Development retry hint must say the PLANNING phase is the producer, not development.

    The plan artifact is a planning-phase *output* / development-phase *input*.
    The retry hint must not claim the development agent forgot to submit it.
    """
    workspace = MemoryWorkspace()
    ctx = _make_ctx(workspace)

    _execution_handler_for("development")(_invoke_effect("development"), ctx)

    hint_content = workspace.read(retry_hint_path("development"))
    assert "planning" in hint_content.lower(), (
        "Development retry hint must name the 'planning' phase as the upstream producer"
    )
    assert "did not submit" not in hint_content, (
        "Development retry hint must not claim the development agent failed to submit the plan"
    )
    assert "input" in hint_content.lower() or "PIPELINE INPUT MISSING" in hint_content, (
        "Development retry hint must indicate the plan is a missing upstream input"
    )


def test_development_missing_dev_result_succeeds_optional() -> None:
    """When development_result is missing, development succeeds (artifact is optional)."""
    workspace = MemoryWorkspace()
    workspace.write(".agent/artifacts/plan.json", _VALID_PLAN_JSON_LEGACY)
    ctx = _make_ctx(workspace)

    events = _execution_handler_for("development")(_invoke_effect("development"), ctx)

    # development_result is optional — missing artifact must not fail the phase
    assert events == [PipelineEvent.AGENT_SUCCESS], (
        "Missing optional development_result must not produce PhaseFailureEvent"
    )
    # No retry hint should be written for a missing optional artifact
    hint_path = retry_hint_path("development")
    assert not workspace.exists(hint_path), (
        "No retry hint should be written for a missing optional artifact"
    )


def test_read_and_clear_retry_hint_returns_content_and_deletes_file() -> None:
    workspace = MemoryWorkspace()
    workspace.write(retry_hint_path("review"), "hint content here")

    result = _read_and_clear_retry_hint(workspace, "review")

    assert result == "hint content here"
    assert not workspace.exists(retry_hint_path("review")), "Hint file must be deleted after read"


def test_read_and_clear_retry_hint_returns_empty_when_absent() -> None:
    workspace = MemoryWorkspace()
    result = _read_and_clear_retry_hint(workspace, "review")
    assert result == ""


@pytest.mark.parametrize(
    "phase",
    [
        p
        for p in REQUIRED_ARTIFACTS
        if p not in {"planning"} and p in _PHASE_TO_HANDLER and p not in _OPTIONAL_ARTIFACTS
    ],
)
def test_retry_hint_content_includes_artifact_info(phase: str) -> None:
    ra = REQUIRED_ARTIFACTS[phase]
    hint = build_retry_hint(phase, "the agent forgot to submit", registry=REQUIRED_ARTIFACTS)
    assert ra.artifact_type in hint, f"Hint for {phase} must mention artifact type"
    assert ra.json_path in hint, f"Hint for {phase} must mention artifact path"


def test_materialize_review_prompt_includes_last_retry_error(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "fix the bug")
    _setup_phase_prerequisites(workspace, "review", full_plan=True)
    workspace.write(retry_hint_path("review"), "PREVIOUS ATTEMPT FAILED: missing issues.json")

    with patch.object(materialize_module, "_git_diff", return_value="diff --git a/x.py"):
        prompt_path = materialize_module.materialize_prompt_for_phase(
            phase="review",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
            workspace_root=tmp_path,
        )

    rendered = workspace.read(prompt_path)
    assert "PREVIOUS ATTEMPT FAILED" in rendered, (
        "Rendered review prompt must include LAST_RETRY_ERROR content when hint file exists"
    )
    assert not workspace.exists(retry_hint_path("review")), (
        "Hint file must be deleted after being read by materialize"
    )


def test_materialize_development_analysis_prompt_includes_last_retry_error(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "implement the plan")
    _setup_phase_prerequisites(workspace, "development_analysis", full_plan=True)
    workspace.write(
        retry_hint_path("development_analysis"),
        "PREVIOUS ATTEMPT FAILED: missing decision artifact",
    )

    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        prompt_path = materialize_module.materialize_prompt_for_phase(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        )

    rendered = workspace.read(prompt_path)
    assert "PREVIOUS ATTEMPT FAILED" in rendered
    assert not workspace.exists(retry_hint_path("development_analysis"))


@pytest.mark.parametrize(
    "phase,drain",
    [
        # development is excluded: development_result is optional, so absence succeeds
        ("review", SessionDrain.REVIEW),
        ("development_analysis", SessionDrain.DEVELOPMENT),
        ("review_analysis", SessionDrain.REVIEW),
    ],
)
def test_end_to_end_retry_flow(tmp_path: Path, phase: str, drain: SessionDrain) -> None:
    """Full retry flow: missing artifact → hint written → prompt includes error → success on retry.

    Drives:
    1. First attempt: handler returns PhaseFailureEvent + writes hint file
    2. Prompt materialization: reads hint, exposes as LAST_RETRY_ERROR, deletes file
    3. Second attempt: handler succeeds with valid artifact present
    """
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "do the work")
    # Use full plan format for tests that call materialize_prompt (triggers handoff rendering)
    _setup_phase_prerequisites(workspace, phase, full_plan=True)

    ctx = _make_ctx(workspace)
    handler = _PHASE_TO_HANDLER[phase]

    # Step 1: first attempt with missing required output artifact
    events = handler(_invoke_effect(), ctx)
    failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
    assert failure_events, f"Phase {phase}: expected PhaseFailureEvent on first attempt"
    assert failure_events[0].recoverable is True
    assert workspace.exists(retry_hint_path(phase)), f"Phase {phase}: hint file must be written"

    # Step 2: write valid artifact, then materialize prompt which surfaces hint as LAST_RETRY_ERROR
    ra = REQUIRED_ARTIFACTS[phase]
    workspace.write(ra.json_path, _PHASE_VALID_ARTIFACT[phase])

    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        prompt_path = materialize_module.materialize_prompt_for_phase(
            phase=phase,
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            artifacts_policy=policy.artifacts,
            session_caps=SessionCapabilities.defaults_for_drain(drain),
            workspace_root=tmp_path,
        )

    rendered = workspace.read(prompt_path)
    assert "PREVIOUS ATTEMPT FAILED" in rendered, (
        f"Phase {phase}: rendered prompt must include LAST_RETRY_ERROR from hint file"
    )
    assert not workspace.exists(retry_hint_path(phase)), (
        f"Phase {phase}: hint file must be deleted after materialize reads it"
    )

    # Step 3: second attempt with valid artifact now present → must advance
    ctx2 = _make_ctx(workspace, policy)
    events2 = handler(_invoke_effect(), ctx2)
    success_events = [
        e for e in events2
        if e in (PipelineEvent.AGENT_SUCCESS, PipelineEvent.ANALYSIS_SUCCESS)
        or isinstance(e, AnalysisDecisionEvent)
    ]
    assert success_events, (
        f"Phase {phase}: second attempt must succeed when valid artifact is present"
    )
