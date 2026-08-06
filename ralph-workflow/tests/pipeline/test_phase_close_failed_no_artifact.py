"""Regression tests for F6 phase-close grading (S-3, closes
PLANNING_ANALYSIS_DECISION.md PA-001's reachability finding).

PA-001 found that the prior draft's render branch was nested inside
``_render_phase_artifact_handoff``, a function only ever called on
``PipelineEvent.AGENT_SUCCESS`` (both ``runner.py:1220`` and
``worker_runtime.py:440`` gate the call on that event) -- so a branch added
inside it could never fire for the exact 2026-08-06-shaped run the brief
names (an agent invocation that never produces the required artifact).

S-2 fixed the reachability defect by restructuring the OUTER call sites
themselves (``runner.py``'s ``_finalize_agent_invocation`` helper and
``worker_runtime.py``'s ``run_parallel_worker_from_manifest``) to call the
new ``render_phase_failure_report`` directly on a non-success event, not
through the success-gated function. These tests exercise that restructured
call site -- not the render helper in isolation -- so a regression that
re-nests the branch inside the success-only path fails here.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.mcp.server._wire_ledger import append_wire_record
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline import phase_agent_handler as phase_agent_handler_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


_DEVELOPMENT_RESULT_ARTIFACT_PATH = ".agent/artifacts/development_result.md"


def _required_development_result_artifact(*, artifact_required: bool = True) -> RequiredArtifact:
    return RequiredArtifact(
        phase="development",
        artifact_type="development_result",
        artifact_path=_DEVELOPMENT_RESULT_ARTIFACT_PATH,
        markdown_path=None,
        normalizer=None,
        artifact_required=artifact_required,
    )


class _CapturingDisplay:
    """Minimal display double exposing only ``record_artifact_outcome``."""

    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def record_artifact_outcome(self, produced: str) -> None:
        self.outcomes.append(produced)


class _StubPolicyBundle:
    """``resolve_phase_required_artifact`` is monkeypatched in these tests, so
    ``.pipeline`` / ``.artifacts`` only need to exist as attributes -- the
    real function is never called with them."""

    pipeline = object()
    artifacts = object()


def test_render_phase_failure_report_emits_failed_no_artifact_for_missing_receipt(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """``render_phase_failure_report`` grades a missing receipt as FAILED (F6 / DoD 12).

    No receipt, no sentinel, and no wire-ledger row exist for ``run_id`` in
    ``tmp_path`` -- ``evaluate_completion`` must return
    ``required_artifact_present.holds is False`` and ``graded_phase_verdict``
    must report ``FAILED``, naming the missing artifact type.
    """
    required_artifact = _required_development_result_artifact()
    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(ctx: object, method_name: str, *args: object, **kwargs: object) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development",
        prompt_file="p.md",
        drain="development",
    )
    workspace = FsWorkspace(tmp_path)

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=workspace,
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="failure-report-run",
    )

    assert len(captured) == 1, f"expected exactly one emitted line, got {captured}"
    method_name, args = captured[0]
    assert method_name == "emit"
    assert args[0] == "developer"
    message = str(args[1])
    assert "FAILED (no artifact)" in message, message
    assert "development_result" in message, message


def test_render_phase_failure_report_is_silent_when_artifact_not_required(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """An optional-artifact phase failing is out of scope (S-2 item 8)."""
    required_artifact = _required_development_result_artifact(artifact_required=False)
    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    captured: list[object] = []
    monkeypatch.setattr(
        phase_agent_handler_module,
        "_emit_via_display",
        lambda *a, **kw: captured.append((a, kw)) or True,
    )

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development",
        prompt_file="p.md",
        drain="development",
    )
    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=FsWorkspace(tmp_path),
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="optional-artifact-run",
    )

    assert captured == []


def test_render_success_artifact_reports_pass_when_wire_backed(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A receipt + sentinel each backed by a matching wire-ledger record grades PASS.

    Proves S-2's WIRE upgrade in ``evaluate_completion`` (via the extracted
    ``grade_artifact_submission_evidence`` / ``grade_completion_sentinel_evidence``
    helpers) is reachable from the phase-close success render, not only from
    the smoke gate.
    """
    secret = "phase-close-wire-secret"
    run_id = "phase-close-wire-run"
    artifact_type = "development_result"
    monkeypatch.setenv("RALPH_BROKER_SECRET", secret)

    # Recompute the receipt HMAC locally rather than importing
    # ``completion_receipts._receipt_hmac`` (a private symbol) -- this is
    # the exact formula that module's public ``artifact_receipt_present``
    # verifies against (``hmac.new(secret, f"{run_id}\n{artifact_type}",
    # sha256)``), inlined so this test only imports public API.
    db = RunStateDB(tmp_path)
    receipt_hmac = hmac.new(
        secret.encode(), f"{run_id}\n{artifact_type}".encode(), hashlib.sha256
    ).hexdigest()
    db.upsert_receipt(run_id, artifact_type, receipt_hmac)
    sentinel_hmac = hmac.new(secret.encode(), run_id.encode(), hashlib.sha256).hexdigest()
    db.upsert_completion_sentinel(run_id, sentinel_hmac)
    db.close()

    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"artifact_type": artifact_type},
        run_id=run_id,
        secret=secret,
    )
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="declare_complete",
        params={"summary": "wire-backed phase close"},
        run_id=run_id,
        secret=secret,
    )

    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / f"{artifact_type}.md").write_text("dummy result", encoding="utf-8")

    required_artifact = _required_development_result_artifact()
    display = _CapturingDisplay()

    phase_agent_handler_module._render_success_artifact(
        artifact_type,
        tmp_path,
        make_display_context(),
        display,
        Verbosity.VERBOSE,
        required_artifact,
        run_id=run_id,
    )

    assert len(display.outcomes) == 1
    assert display.outcomes[0].endswith("— PASS"), display.outcomes[0]


def test_finalize_agent_invocation_reaches_failure_report_on_agent_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    """The real runner.py call site reaches the FAILED render on AGENT_FAILURE.

    Drives ``runner_module._finalize_agent_invocation`` -- the exact function
    ``_run_pipeline_step``'s loop calls unconditionally for an
    ``InvokeAgentEffect`` -- with ``event=AGENT_FAILURE``, and asserts
    ``render_phase_failure_report`` (not ``phase_event_after_agent_run``) is
    reached, carrying the same ``run_id``. This is the property PA-001
    flagged as unproven under the prior draft's nesting.
    """
    captured: dict[str, object] = {}

    def _fake_phase_event_after_agent_run(**kwargs: object) -> PipelineEvent:
        captured["phase_event_after_agent_run_called"] = True
        del kwargs
        return PipelineEvent.AGENT_SUCCESS

    def _fake_render_phase_failure_report(effect: object, **kwargs: object) -> None:
        captured["render_phase_failure_report_effect"] = effect
        captured["render_phase_failure_report_run_id"] = kwargs.get("run_id")

    monkeypatch.setattr(
        runner_module, "phase_event_after_agent_run", _fake_phase_event_after_agent_run
    )
    monkeypatch.setattr(
        runner_module, "render_phase_failure_report", _fake_render_phase_failure_report
    )

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development",
        prompt_file="p.md",
        drain="development",
    )
    state = PipelineState(phase="development")

    new_state, new_event = runner_module._finalize_agent_invocation(
        effect=effect,
        event=PipelineEvent.AGENT_FAILURE,
        state=state,
        config=object(),
        policy_bundle=object(),
        workspace=object(),
        workspace_scope=WorkspaceScope(Path("/tmp/does-not-matter")),
        display=None,
        display_context=None,
        verbosity=Verbosity.VERBOSE,
        recovery_controller=None,
        run_id="finalize-failure-run",
    )

    assert "phase_event_after_agent_run_called" not in captured, (
        "phase_event_after_agent_run must NOT be called on AGENT_FAILURE"
    )
    assert captured["render_phase_failure_report_effect"] is effect
    assert captured["render_phase_failure_report_run_id"] == "finalize-failure-run"
    assert new_event == PipelineEvent.AGENT_FAILURE, (
        "the failure-report render must not mutate the event the reducer sees"
    )
    assert isinstance(new_state, PipelineState)


def test_finalize_agent_invocation_threads_run_id_on_success_branch(
    monkeypatch: MonkeyPatch,
) -> None:
    """The success branch still threads run_id and does not call the failure report."""
    captured: dict[str, object] = {}

    def _fake_phase_event_after_agent_run(**kwargs: object) -> PipelineEvent:
        captured["run_id"] = kwargs.get("run_id")
        return PipelineEvent.AGENT_SUCCESS

    def _fake_render_phase_failure_report(effect: object, **kwargs: object) -> None:
        del effect, kwargs
        captured["failure_report_called"] = True

    monkeypatch.setattr(
        runner_module, "phase_event_after_agent_run", _fake_phase_event_after_agent_run
    )
    monkeypatch.setattr(
        runner_module, "render_phase_failure_report", _fake_render_phase_failure_report
    )

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development",
        prompt_file="p.md",
        drain="development",
    )
    state = PipelineState(phase="development")

    _, new_event = runner_module._finalize_agent_invocation(
        effect=effect,
        event=PipelineEvent.AGENT_SUCCESS,
        state=state,
        config=object(),
        policy_bundle=object(),
        workspace=object(),
        workspace_scope=WorkspaceScope(Path("/tmp/does-not-matter")),
        display=None,
        display_context=None,
        verbosity=Verbosity.VERBOSE,
        recovery_controller=None,
        run_id="finalize-success-run",
    )

    assert "failure_report_called" not in captured
    assert captured["run_id"] == "finalize-success-run"
    assert new_event == PipelineEvent.AGENT_SUCCESS
