"""Context for artifact handoff rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.config.enums import Verbosity

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle


@dataclass(frozen=True)
class ArtifactHandoffContext:
    """Optional context for render_phase_artifact_handoff."""

    display_context: DisplayContext | None = None
    verbosity: Verbosity = Verbosity.VERBOSE
    drain: str | None = None
    policy_bundle: PolicyBundle | None = None
    state: PipelineState | None = None
