"""Plan S-8: the multimodal handoff reader must cover divergent drain keys."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.policy.loader import load_policy
from ralph.prompts.debug_dump import (
    collect_media_entries_for_phase,
    media_session_path,
    multimodal_sidecar_path,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.fs import FsWorkspace

# Phases whose configured drain differs from the phase name (drain-name key
# mismatch the reader must close). The set is taken from the canonical
# `ralph/policy/defaults/pipeline.toml` and is the only divergence Ralph can
# see at this writing.
DIVERGENT_PHASES: tuple[str, str] = (
    ("development_commit_cleanup", "commit"),
    ("development_final_commit", "development_commit"),
    ("development_final_commit_cleanup", "commit"),
)


def _payload(artifact_id: str, title: str) -> dict[str, str]:
    return {
        "schema_version": "2",
        "phase": "ignored",
        "artifacts": [
            {
                "artifact_id": artifact_id,
                "uri": f"ralph://media/{artifact_id}",
                "mime_type": "image/png",
                "title": title,
                "modality": "image",
                "delivery": "inline_image",
                "reason": "test fixture",
                "source_path": title,
                "cache_path": f".agent/tmp/media/{artifact_id}",
                "source_uri": "",
                "block_type": "",
                "identity_key": f"source-path:image:{title}",
            }
        ],
    }


def _seed_index(workspace_root: Path, drain: str, artifact_id: str, title: str) -> None:
    path = workspace_root / media_session_path(drain)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_payload(artifact_id, title)), encoding="utf-8")


def test_collect_media_entries_reads_divergent_drain_keys(tmp_path: Path) -> None:
    """`collect_media_entries_for_phase` covers drain-keyed entries for divergent phases."""
    for phase, drain in DIVERGENT_PHASES:
        workspace = FsWorkspace(tmp_path)
        _seed_index(tmp_path, drain, f"{phase}-entry", f"{phase}.png")
        entries = collect_media_entries_for_phase(workspace, phase, drain=drain)
        assert len(entries) == 1, (
            f"phase {phase!r} (drain {drain!r}) must return its drain-keyed entry"
        )
        assert entries[0].artifact_id == f"{phase}-entry"


def test_materialize_prompt_carries_drain_keyed_entries_for_divergent_phases(
    tmp_path: Path,
) -> None:
    """`materialize_prompt_for_phase` for a divergent phase surfaces the drain-keyed media."""
    policy = load_policy(tmp_path / ".agent")
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Build the feature\n")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    for phase, drain in DIVERGENT_PHASES:
        _seed_index(tmp_path, drain, f"{phase}-entry", f"{phase}.png")
        entries = collect_media_entries_for_phase(workspace, phase, drain=drain)
        assert entries, f"phase {phase!r} must see its drain-keyed media before materialization"

        spy_entries: list = []

        def spy(
            context: PromptPhaseContext,
            options: PromptPhaseOptions,
            _sink: list = spy_entries,
        ) -> str:
            _sink.append(list(options.multimodal_entries or ()))
            return f"# prompt for {context.phase}\n"

        from ralph.prompts import materialize as materialize_module

        materialize_spy = spy
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                materialize_module,
                "_render_prompt_for_phase",
                materialize_spy,
            )
            materialize_prompt_for_phase(
                PromptPhaseContext(
                    phase=phase,
                    workspace=workspace,
                    pipeline_policy=policy.pipeline,
                    session_caps=SessionCapabilities.defaults_for_drain(
                        SessionDrain.DEVELOPMENT
                    ),
                    workspace_root=tmp_path,
                ),
                PromptPhaseOptions(multimodal_entries=entries),
            )

        sidecar = json.loads(workspace.read(multimodal_sidecar_path(phase)))
        assert sidecar["phase"] == phase
        assert any(
            artifact["artifact_id"] == f"{phase}-entry"
            for artifact in sidecar["artifacts"]
        ), f"phase {phase!r} sidecar must list the drain-keyed entry"
        # The reader must have surfaced the drain-keyed entry to the spy.
        assert spy_entries and any(
            entry.artifact_id == f"{phase}-entry" for entry in spy_entries[-1]
        ), f"phase {phase!r} spy must see the drain-keyed media entry"
        # Wipe the index so the next iteration starts from a clean workspace.
        (tmp_path / media_session_path(drain)).unlink()
        (tmp_path / multimodal_sidecar_path(phase)).unlink()


def test_materialize_prompt_merges_phase_and_drain_entries(tmp_path: Path) -> None:
    """A phase-keyed entry is still returned alongside a drain-keyed one for a divergent phase."""
    phase, drain = DIVERGENT_PHASES[0]
    policy = load_policy(tmp_path / ".agent")
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Build the feature\n")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    _seed_index(tmp_path, drain, f"{phase}-drain", f"{phase}-drain.png")
    _seed_index(tmp_path, phase, f"{phase}-phase", f"{phase}-phase.png")

    entries = collect_media_entries_for_phase(workspace, phase, drain=drain)
    artifact_ids = {entry.artifact_id for entry in entries}
    expected = {f"{phase}-drain", f"{phase}-phase"}
    assert artifact_ids == expected, (
        f"reader must return both drain- and phase-keyed entries for a divergent phase "
        f"(phase={phase!r}, drain={drain!r})"
    )

    from ralph.prompts import materialize as materialize_module

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            materialize_module,
            "_render_prompt_for_phase",
            lambda context, options: f"# prompt for {context.phase}\n",
        )
        materialize_prompt_for_phase(
            PromptPhaseContext(
                phase=phase,
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(
                    SessionDrain.DEVELOPMENT
                ),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(multimodal_entries=entries),
        )
    sidecar = json.loads(workspace.read(multimodal_sidecar_path(phase)))
    sidecar_ids = {artifact["artifact_id"] for artifact in sidecar["artifacts"]}
    assert sidecar_ids == {f"{phase}-drain", f"{phase}-phase"}


def test_materialize_prompt_dedupes_duplicate_phase_and_drain_entries(tmp_path: Path) -> None:
    """An entry present under both keys must appear exactly once."""
    phase, drain = DIVERGENT_PHASES[0]
    policy = load_policy(tmp_path / ".agent")
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Build the feature\n")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStep 1.\n")

    _seed_index(tmp_path, drain, "shared", "shared.png")
    _seed_index(tmp_path, phase, "shared", "shared.png")

    entries = collect_media_entries_for_phase(workspace, phase, drain=drain)
    assert [entry.artifact_id for entry in entries] == ["shared"], (
        "duplicate identity_key entries must collapse to one"
    )

    from ralph.prompts import materialize as materialize_module

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            materialize_module,
            "_render_prompt_for_phase",
            lambda context, options: f"# prompt for {context.phase}\n",
        )
        materialize_prompt_for_phase(
            PromptPhaseContext(
                phase=phase,
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(
                    SessionDrain.DEVELOPMENT
                ),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(multimodal_entries=entries),
        )
    sidecar = json.loads(workspace.read(multimodal_sidecar_path(phase)))
    assert [artifact["artifact_id"] for artifact in sidecar["artifacts"]] == ["shared"]


def test_default_policy_lists_divergent_phases() -> None:
    """Pin the canonical divergent phases the reader must close."""
    policy = load_policy(Path("ralph/policy/defaults"))
    resolved = {
        phase: resolve_phase_drain(phase, policy.pipeline) or phase
        for phase, _ in DIVERGENT_PHASES
    }
    assert resolved == {
        "development_commit_cleanup": "commit",
        "development_final_commit": "development_commit",
        "development_final_commit_cleanup": "commit",
    }
