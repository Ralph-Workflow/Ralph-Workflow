"""S-4 (G4 / DoD 15): a corrupted raw transcript reaches BOTH non-smoke
phase-verdict outcomes -- not only the smoke gate.

``_detect_smoke_errors`` (``tests/test_raw_transcript_corruption_detection.py``)
proves the smoke seam. This module proves the shared, non-smoke seam:
``phase_agent_handler._compute_graded_phase_verdict`` is the one function
called by BOTH operator-facing phase-verdict renderers --
``render_success_artifact`` (the PASS/DEGRADED path) and
``render_phase_failure_report`` (the FAILED path, DoD 12's machinery).
Wiring the break check into that one shared function covers every
artifact-producing phase's report.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport, Verbosity
from ralph.config.models import AgentConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline import phase_agent_handler as phase_agent_handler_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def _agy_config() -> AgentConfig:
    return AgentConfig(cmd="agy", transport=AgentTransport.AGY)


def _claude_interactive_config() -> AgentConfig:
    return AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)


def _write_corrupted_raw_log(workspace_root: Path) -> None:
    """Write a raw log at the exact path ``AgentConfig(cmd="agy")`` resolves to."""
    raw_path = workspace_root / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b'{"event":"init","tools":["call_mcp_tool"]}\n' + (b"\x00" * 512))


def _write_valid_claude_raw_log(workspace_root: Path) -> None:
    """Write a valid Claude interactive raw log at the path for ``cmd="claude"``."""
    raw_path = workspace_root / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90\n"
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
    )


def test_render_phase_failure_report_names_raw_log_corruption(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """The FAILED (no artifact) path also names a corrupted raw transcript.

    No receipt exists for ``run_id`` (a genuine required-artifact failure,
    the same DoD-12 shape ``tests/pipeline/test_phase_close_failed_no_artifact.py``
    pins) AND the raw transcript for this agent's config is corrupted. Both
    facts must reach the rendered verdict line.
    """
    _write_corrupted_raw_log(tmp_path)

    required_artifact = RequiredArtifact(
        phase="plan",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )

    class _StubPolicyBundle:
        pipeline = object()
        artifacts = object()

    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="agy/gemini-3.6-flash-low",
        phase="plan",
        prompt_file="planning_prompt.md",
        drain="plan",
    )
    workspace = FsWorkspace(tmp_path)

    from ralph.config.models import GeneralConfig, UnifiedConfig

    unified_config = UnifiedConfig(
        general=GeneralConfig(), agents={"agy/gemini-3.6-flash-low": _agy_config()}
    )

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=workspace,
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="raw-log-break-failure-run",
        config=unified_config,
    )

    assert len(captured) == 1, f"expected exactly one rendered verdict line, got {captured}"
    message = str(captured[0][1][1])
    assert "FAILED (no artifact)" in message, message
    assert "raw transcript corrupted:" in message, message
    assert "NUL-byte run" in message, message


def test_render_phase_failure_report_omits_corruption_when_config_not_supplied(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """``config`` is optional and additive: omitting it (pre-S-4 call shape)
    reproduces the pre-S-4 text exactly -- no crash, no corruption text."""
    _write_corrupted_raw_log(tmp_path)

    required_artifact = RequiredArtifact(
        phase="plan",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )

    class _StubPolicyBundle:
        pipeline = object()
        artifacts = object()

    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="agy/gemini-3.6-flash-low",
        phase="plan",
        prompt_file="planning_prompt.md",
        drain="plan",
    )
    workspace = FsWorkspace(tmp_path)

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=workspace,
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="raw-log-break-no-config-run",
    )

    assert len(captured) == 1
    message = str(captured[0][1][1])
    assert message == "Verdict: FAILED (no artifact) — no receipt for 'plan'; .agent/artifacts/plan.md absent"
    assert "raw transcript corrupted:" not in message


def test_render_success_artifact_names_raw_log_corruption(tmp_path: Path) -> None:
    """The PASS/DEGRADED path (``render_success_artifact``) also names a
    corrupted raw transcript, not only the FAILED path above.

    ``artifact_required=False`` sidesteps the FAILED branch entirely (no
    receipt exists in ``tmp_path`` either way) so the render goes through
    ``graded_verdict``'s PASS/DEGRADED vocabulary -- proving the corruption
    detail reaches that branch's rendered text too, independently of the
    FAILED-path proof above.
    """
    _write_corrupted_raw_log(tmp_path)

    required_artifact = RequiredArtifact(
        phase="plan",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=False,
    )

    class _CapturingDisplay:
        def __init__(self) -> None:
            self.outcomes: list[str] = []

        def record_artifact_outcome(self, produced: str) -> None:
            self.outcomes.append(produced)

    display = _CapturingDisplay()

    phase_agent_handler_module.render_success_artifact(
        "plan",
        tmp_path,
        make_display_context(),
        display,
        Verbosity.VERBOSE,
        required_artifact,
        run_id="raw-log-break-success-run",
        agent_config=_agy_config(),
    )

    assert display.outcomes, "render_success_artifact must record an artifact outcome"
    outcome = display.outcomes[0]
    assert "raw transcript corrupted:" in outcome, outcome
    assert "NUL-byte run" in outcome, outcome


def test_render_phase_failure_report_omits_corruption_for_valid_claude_transcript(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A valid Claude interactive raw transcript must not be diagnosed as corrupted.

    The reported failure combined ``FAILED (no artifact)`` with
    ``raw transcript corrupted`` because the ``Session ID:`` line was
    graded NON_JSONL. With a clean raw capture the verdict must stay
    ``FAILED (no artifact)`` without any corruption text.
    """
    _write_valid_claude_raw_log(tmp_path)

    required_artifact = RequiredArtifact(
        phase="plan",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )

    class _StubPolicyBundle:
        pipeline = object()
        artifacts = object()

    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="claude/haiku",
        phase="plan",
        prompt_file="planning_prompt.md",
        drain="plan",
    )
    workspace = FsWorkspace(tmp_path)

    from ralph.config.models import GeneralConfig, UnifiedConfig

    unified_config = UnifiedConfig(
        general=GeneralConfig(),
        agents={"claude/haiku": _claude_interactive_config()},
    )

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=workspace,
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="valid-claude-transcript-run",
        config=unified_config,
    )

    assert len(captured) == 1, f"expected exactly one rendered verdict line, got {captured}"
    message = str(captured[0][1][1])
    assert "FAILED (no artifact)" in message, message
    assert "raw transcript corrupted:" not in message, message


def test_render_phase_failure_report_omits_corruption_for_ansi_wrapped_claude_transcript(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """ANSI-wrapped ``Session ID:`` lines must not be diagnosed as corrupted.

    The interactive Claude TUI styles the session banner; the phase
    verdict seam must normalize the line before grading.
    """
    raw_path = tmp_path / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"\x1b[2mSession ID: 28ee58c0-0614-474f-b609-80cc6c252f90\x1b[22m\n"
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
    )

    required_artifact = RequiredArtifact(
        phase="plan",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )

    class _StubPolicyBundle:
        pipeline = object()
        artifacts = object()

    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="claude/haiku",
        phase="plan",
        prompt_file="planning_prompt.md",
        drain="plan",
    )
    workspace = FsWorkspace(tmp_path)

    from ralph.config.models import GeneralConfig, UnifiedConfig

    unified_config = UnifiedConfig(
        general=GeneralConfig(),
        agents={"claude/haiku": _claude_interactive_config()},
    )

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=workspace,
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="ansi-claude-transcript-run",
        config=unified_config,
    )

    assert len(captured) == 1, f"expected exactly one rendered verdict line, got {captured}"
    message = str(captured[0][1][1])
    assert "FAILED (no artifact)" in message, message
    assert "raw transcript corrupted:" not in message, message


def test_render_phase_failure_report_omits_corruption_for_visible_pty_tool_output(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Visible tool/file output from an interactive Claude PTY is not corruption.

    The live ``claude/haiku`` smoke raw log includes rendered MCP tool
    status lines and file-content echoes. The phase verdict seam passes
    the interactive transport to ``detect_raw_log_breaks`` so these
    human-visible lines do not get folded into ``FAILED (no artifact)``.
    """
    raw_path = tmp_path / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90\n"
        b"\xe2\x9c\x93 PASS \xe2\x86\xb3 ralph.write_file (path=tmp/interactive-claude-smoke/todo-list.js)\n"
        b"const todos = [];\n"
        b"module.exports = TodoAPI;\n"
        b"Task declared complete: session_id=28ee58c0-0614-474f-b609-80cc6c252f90\n"
    )

    required_artifact = RequiredArtifact(
        phase="plan",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )

    class _StubPolicyBundle:
        pipeline = object()
        artifacts = object()

    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="claude/haiku",
        phase="plan",
        prompt_file="planning_prompt.md",
        drain="plan",
    )
    workspace = FsWorkspace(tmp_path)

    from ralph.config.models import GeneralConfig, UnifiedConfig

    unified_config = UnifiedConfig(
        general=GeneralConfig(),
        agents={"claude/haiku": _claude_interactive_config()},
    )

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=workspace,
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="visible-pty-output-run",
        config=unified_config,
    )

    assert len(captured) == 1, f"expected exactly one rendered verdict line, got {captured}"
    message = str(captured[0][1][1])
    assert "FAILED (no artifact)" in message, message
    assert "raw transcript corrupted:" not in message, message
