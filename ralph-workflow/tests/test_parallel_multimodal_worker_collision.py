from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.policy.loader import load_policy
from ralph.prompts._multimodal_sidecar_entry import MultimodalSidecarEntry
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_parallel_workers_use_namespaced_multimodal_sidecars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Base development prompt")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Implement the assigned unit\n")

    monkeypatch.setattr(
        "ralph.prompts.materialize._render_prompt_for_phase",
        lambda *_args, **_kwargs: "Base development prompt",
    )

    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        worker_namespace=tmp_path / ".agent" / "workers" / "unit-multimodal-a",
        multimodal_entries=[
            MultimodalSidecarEntry(
                artifact_id="art-a",
                uri="artifact://art-a",
                mime_type="image/png",
                title="Unit A screenshot",
                modality="image",
                delivery="resource_reference_replay",
            )
        ],
    )

    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        worker_namespace=tmp_path / ".agent" / "workers" / "unit-multimodal-b",
        multimodal_entries=[
            MultimodalSidecarEntry(
                artifact_id="art-b",
                uri="artifact://art-b",
                mime_type="image/png",
                title="Unit B screenshot",
                modality="image",
                delivery="resource_reference_replay",
            )
        ],
    )

    sidecar_a = workspace.read(
        str(
            tmp_path
            / ".agent"
            / "workers"
            / "unit-multimodal-a"
            / "tmp"
            / "development_multimodal_handoff.json"
        )
    )
    sidecar_b = workspace.read(
        str(
            tmp_path
            / ".agent"
            / "workers"
            / "unit-multimodal-b"
            / "tmp"
            / "development_multimodal_handoff.json"
        )
    )

    assert not workspace.exists(".agent/tmp/development_multimodal_handoff.json")
    assert '"artifact_id": "art-a"' in sidecar_a
    assert '"artifact_id": "art-b"' not in sidecar_a
    assert '"artifact_id": "art-b"' in sidecar_b
    assert '"artifact_id": "art-a"' not in sidecar_b
