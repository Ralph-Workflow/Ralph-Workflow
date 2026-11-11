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
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def record_artifact_outcome(self, produced: str) -> None:
        self.outcomes.append(produced)


class _StubPolicyBundle:
    pipeline = object()
    artifacts = object()


def test_render_phase_failure_report_emits_failed_no_artifact_for_missing_receipt(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    required_artifact = _required_development_result_artifact()
    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
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
    secret = "phase-close-wire-secret"
    run_id = "phase-close-wire-run"
    artifact_type = "development_result"
    monkeypatch.setenv("RALPH_BROKER_SECRET", secret)

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
