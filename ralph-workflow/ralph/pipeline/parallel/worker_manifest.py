from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ralph.pipeline.work_units import WorkUnit
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from pathlib import Path


class ParallelWorkerManifest(RalphBaseModel):
    unit_id: str
    description: str
    allowed_directories: list[str]
    paths: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    phase: str
    drain: str
    config_path: str | None = None
    cli_overrides: dict[str, object] = Field(default_factory=dict)
    worker_namespace: str
    worker_artifact_dir: str
    prompt_file: str
    workspace_root: str

    @classmethod
    def load(cls, manifest_path: Path) -> ParallelWorkerManifest:
        return cls.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    def to_work_unit(self) -> WorkUnit:

        return WorkUnit(
            unit_id=self.unit_id,
            description=self.description,
            allowed_directories=list(self.allowed_directories),
            paths=list(self.paths),
            step_ids=list(self.step_ids),
        )
