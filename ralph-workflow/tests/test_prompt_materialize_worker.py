from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("legacy_field", ["work_unit", "worker_namespace"])
def test_deprecated_worker_prompt_inputs_crash(tmp_path: Path, legacy_field: str) -> None:
    workspace = MemoryWorkspace(root=tmp_path)
    workspace.write("PROMPT.md", "Implement the requested behavior.")
    policy = load_policy(tmp_path / ".agent")
    legacy_value: object = (
        WorkUnit(unit_id="api", description="API", paths=["src/api.py"])
        if legacy_field == "work_unit"
        else tmp_path / ".agent" / "workers" / "api"
    )

    with pytest.raises(ValueError, match="worker and work-unit execution is deprecated"):
        materialize_prompt_for_phase(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
            artifacts_policy=policy.artifacts,
            **{legacy_field: legacy_value},
        )
