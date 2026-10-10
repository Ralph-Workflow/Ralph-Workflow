# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Typed manifest model for a single parallel worker run.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ralph.pipeline.work_units import WorkUnit
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from pathlib import Path


class ParallelWorkerManifest(RalphBaseModel):
    """Serializable manifest describing one isolated worker invocation.

    .. deprecated::
        Deprecated: Ralph-orchestrated workers are removed from the execution model;
        parallelism is owned by the developer agent's own sub-agents. See
        docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
    """

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
        """Load manifest from json file.

        .. deprecated::
            Deprecated: Ralph-orchestrated workers are removed from the execution model;
            parallelism is owned by the developer agent's own sub-agents. See
            docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
        """
        return cls.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    def to_work_unit(self) -> WorkUnit:
        """Return the manifest payload as a WorkUnit for worker execution.

        .. deprecated::
            Deprecated: Ralph-orchestrated workers are removed from the execution model;
            parallelism is owned by the developer agent's own sub-agents. See
            docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
        """

        return WorkUnit(
            unit_id=self.unit_id,
            description=self.description,
            allowed_directories=list(self.allowed_directories),
            paths=list(self.paths),
            step_ids=list(self.step_ids),
        )
