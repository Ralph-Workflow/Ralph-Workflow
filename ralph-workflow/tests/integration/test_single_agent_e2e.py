from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
from ralph.mcp.multimodal.capabilities import (
    DeliveryMode,
    MultimodalModelIdentity,
    resolve_capability_profile,
)
from ralph.pipeline import runner as runner_module
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.debug_dump import media_session_path
from ralph.prompts.materialize import collect_media_entries_for_phase
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _policy_bundle() -> SimpleNamespace:
    agents = AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["planner"]),
            "development": AgentChainConfig(agents=["developer"]),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
        },
    )
    pipeline = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
    )
    return SimpleNamespace(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())


def test_run_completes_in_serial_mode_without_fan_out(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = tmp_git_repo / "PROMPT.md"
    prompt.write_text("# Test Prompt\n\nRun the serial path.")

    initial_state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(agents=["planner"]),
            "development": AgentChainState(agents=["developer"]),
        },
        work_units=(),
    )

    handled_phases: list[str] = []
    saved_states: list[PipelineState] = []

    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: WorkspaceScope(tmp_git_repo),
    )
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: _policy_bundle())
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module,
        "materialize_agent_prompt_if_needed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    def _save_state(state: PipelineState) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner_module.ckpt, "save", _save_state)

    def _fake_execute_effect(
        effect: object,
        config: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del config, workspace_scope, kwargs
        handled_phases.append(effect.phase)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        runner_module,
        "_execute_effect_with_optional_display",
        _fake_execute_effect,
    )
    monkeypatch.setattr(
        runner_module,
        "execute_fan_out_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("fan-out should not run")),
    )

    exit_code = runner_module.run(
        config=MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
    )

    assert exit_code == 0
    assert handled_phases == ["planning", "development"]
    assert saved_states[-1].phase == "complete"


def test_serial_run_completes_when_development_phase_encounters_multimodal_tool_output(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial unattended run reaches 'complete' even when the development phase produces
    multimodal placeholder output (e.g. '[image: image/png]') from the parser.

    This is the black-box proof that the managed runtime path survives multimodal
    tool results: the pipeline must not emit a fan-out event or get stuck when the
    development phase processes multimodal tool output.
    """
    prompt = tmp_git_repo / "PROMPT.md"
    prompt.write_text("# Test\n\nRun multimodal serial path.")

    initial_state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(agents=["planner"]),
            "development": AgentChainState(agents=["developer"]),
        },
        work_units=(),
    )

    handled_phases: list[str] = []
    saved_states: list[PipelineState] = []
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: WorkspaceScope(tmp_git_repo),
    )
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: _policy_bundle())
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module,
        "materialize_agent_prompt_if_needed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    def _save_state(state: PipelineState) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner_module.ckpt, "save", _save_state)

    def _fake_execute_effect(
        effect: object,
        config: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del config, workspace_scope, kwargs
        handled_phases.append(effect.phase)
        # Simulate successful completion even when the development phase encountered
        # multimodal placeholder output like '[image: image/png]' from the parser.
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        runner_module,
        "_execute_effect_with_optional_display",
        _fake_execute_effect,
    )
    monkeypatch.setattr(
        runner_module,
        "execute_fan_out_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("fan-out must not run")),
    )

    exit_code = runner_module.run(
        config=MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
    )

    assert exit_code == 0
    assert "development" in handled_phases
    assert saved_states[-1].phase == "complete"


def test_development_phase_receives_multimodal_handoff_metadata(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner-owned prompt seam carries delivery/block_type/URI metadata to dev phase.

    Proves that when a media session index is written by the MCP server before
    the development phase runs, the runner reads it via collect_media_entries_for_phase
    and the captured entries preserve delivery mode, block_type, and ralph://media/...
    replay identity from the capability-profile verdict.
    """
    # Write a media session index as the MCP server would during a live session.
    # Claude provider: image=inline_image, pdf=typed_block.
    claude_identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet")
    profile = resolve_capability_profile(claude_identity)
    image_verdict = profile.verdict_for("image")
    pdf_verdict = profile.verdict_for("pdf")

    index_payload = {
        "schema_version": "2",
        "phase": "development",
        "artifacts": [
            {
                "artifact_id": "img-handoff-001",
                "uri": "ralph://media/img-handoff-001",
                "mime_type": "image/png",
                "title": "screenshot.png",
                "modality": "image",
                "delivery": image_verdict.delivery.value,
                "reason": image_verdict.reason,
                "source_path": "screens/cap.png",
                "cache_path": "",
                "source_uri": "",
                "block_type": image_verdict.block_type or "",
            },
            {
                "artifact_id": "pdf-handoff-002",
                "uri": "ralph://media/pdf-handoff-002",
                "mime_type": "application/pdf",
                "title": "report.pdf",
                "modality": "pdf",
                "delivery": pdf_verdict.delivery.value,
                "reason": pdf_verdict.reason,
                "source_path": "reports/report.pdf",
                "cache_path": ".agent/tmp/media/report.pdf",
                "source_uri": "",
                "block_type": pdf_verdict.block_type or "",
            },
        ],
    }
    index_path = tmp_git_repo / media_session_path("development")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_payload), encoding="utf-8")

    # Spy on materialize_agent_prompt_if_needed to capture what collect_media_entries_for_phase
    # returns for the development phase — without actually rendering templates.

    captured_entries: list[object] = []

    def _spy_materialize(
        effect: object, state: object, workspace: object, *args: object, **kw: object
    ) -> None:
        phase = getattr(effect, "phase", None)
        if phase is not None:
            fs_ws = FsWorkspace(tmp_git_repo)
            entries = collect_media_entries_for_phase(fs_ws, str(phase))
            captured_entries.extend(entries)

    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", _spy_materialize)

    initial_state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(agents=["planner"]),
            "development": AgentChainState(agents=["developer"]),
        },
        work_units=(),
    )

    saved_states: list[PipelineState] = []
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: WorkspaceScope(tmp_git_repo),
    )
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: _policy_bundle())
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(runner_module.ckpt, "save", saved_states.append)
    monkeypatch.setattr(
        runner_module,
        "_execute_effect_with_optional_display",
        lambda effect, config, workspace_scope, **kw: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module,
        "execute_fan_out_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("fan-out must not run")),
    )

    exit_code = runner_module.run(
        config=MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
    )

    assert exit_code == 0
    assert saved_states[-1].phase == "complete"

    # (a) development phase received multimodal entries via the runner-owned seam.
    dev_entries = [e for e in captured_entries if hasattr(e, "uri")]
    assert len(dev_entries) >= 2, (
        f"Expected at least 2 multimodal entries for development phase, got {len(dev_entries)}: "
        f"{dev_entries}"
    )

    uris = {e.uri for e in dev_entries}
    assert "ralph://media/img-handoff-001" in uris, (
        f"Expected image replay URI in entries, got URIs: {uris}"
    )
    assert "ralph://media/pdf-handoff-002" in uris, (
        f"Expected PDF replay URI in entries, got URIs: {uris}"
    )

    image_entry = next(e for e in dev_entries if e.uri == "ralph://media/img-handoff-001")
    pdf_entry = next(e for e in dev_entries if e.uri == "ralph://media/pdf-handoff-002")

    # (b) delivery metadata and replay identity survive from the managed decision into handoff.
    assert image_entry.delivery == DeliveryMode.INLINE_IMAGE.value, (
        f"Image entry must have inline_image delivery, got {image_entry.delivery!r}"
    )
    assert pdf_entry.delivery == DeliveryMode.TYPED_BLOCK.value, (
        f"PDF entry must have typed_block delivery, got {pdf_entry.delivery!r}"
    )
    assert pdf_entry.block_type == "pdf", (
        f"PDF entry must carry block_type='pdf', got {pdf_entry.block_type!r}"
    )
    assert image_entry.reason, (
        "Image entry must have a non-empty reason from the capability verdict"
    )


def test_unsupported_modality_surfaces_explicit_rejection_through_runner_path(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported provider/modality combinations are carried through the runner handoff seam.

    Proves (c) from plan Step 1: when an unsupported modality is encountered for a
    known provider (e.g. audio for Claude), the media session index written by the
    MCP server carries unsupported delivery and the runner reads this through the
    collect_media_entries_for_phase seam rather than silently dropping the entry.
    """
    # Claude does not support audio — simulate the MCP server writing an unsupported entry.
    claude_identity = MultimodalModelIdentity(provider="claude")
    profile = resolve_capability_profile(claude_identity)
    audio_verdict = profile.verdict_for("audio")

    assert audio_verdict.delivery == DeliveryMode.UNSUPPORTED, (
        "Pre-condition: audio must be UNSUPPORTED for Claude"
    )

    # Write a session index that records the unsupported audio artifact.
    index_payload = {
        "schema_version": "2",
        "phase": "development",
        "artifacts": [
            {
                "artifact_id": "aud-unsupported-001",
                "uri": "ralph://media/aud-unsupported-001",
                "mime_type": "audio/mpeg",
                "title": "clip.mp3",
                "modality": "audio",
                "delivery": audio_verdict.delivery.value,
                "reason": audio_verdict.reason,
                "source_path": "",
                "cache_path": "",
                "source_uri": "",
                "block_type": "",
            },
        ],
    }
    index_path = tmp_git_repo / media_session_path("development")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_payload), encoding="utf-8")

    captured_entries: list[object] = []

    def _spy_materialize(
        effect: object, state: object, workspace: object, *args: object, **kw: object
    ) -> None:
        phase = getattr(effect, "phase", None)
        if phase is not None:
            fs_ws = FsWorkspace(tmp_git_repo)
            entries = collect_media_entries_for_phase(fs_ws, str(phase))
            captured_entries.extend(entries)

    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", _spy_materialize)

    initial_state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(agents=["planner"]),
            "development": AgentChainState(agents=["developer"]),
        },
        work_units=(),
    )

    saved_states: list[PipelineState] = []
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: WorkspaceScope(tmp_git_repo),
    )
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: _policy_bundle())
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(runner_module.ckpt, "save", saved_states.append)
    monkeypatch.setattr(
        runner_module,
        "_execute_effect_with_optional_display",
        lambda effect, config, workspace_scope, **kw: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module,
        "execute_fan_out_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("fan-out must not run")),
    )

    exit_code = runner_module.run(
        config=MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
    )

    # (d) serial mode still reaches complete without fan-out.
    assert exit_code == 0
    assert saved_states[-1].phase == "complete"

    # (c) the unsupported entry is read through the handoff seam — not silently dropped.
    unsupported_entries = [
        e
        for e in captured_entries
        if hasattr(e, "delivery") and e.delivery == DeliveryMode.UNSUPPORTED.value
    ]
    assert len(unsupported_entries) == 1, (
        f"Expected 1 unsupported entry via the runner seam, got {len(unsupported_entries)}: "
        f"{captured_entries}"
    )
    aud = unsupported_entries[0]
    assert aud.modality == "audio"
    assert "unsupported" in aud.delivery
    assert aud.reason, "Unsupported entry must carry a non-empty reason from the capability verdict"
