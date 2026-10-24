"""Phase-output artifact helpers for the commit executor.

The helpers in this module own the phase artifact selection and cleanup
contract used by the pipeline runner.  They live beside the commit executor's
public compatibility surface so the executor module stays below the
repository's file-size limit without changing any caller-facing names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.phases.required_artifacts import build_required_artifacts

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy, PolicyBundle


def phase_output_artifact_paths(
    phase: str, *, drain: str | None = None, policy_bundle: PolicyBundle | None = None
) -> tuple[str, ...]:
    """Return paths of all output artifacts produced by a phase."""
    paths: list[str] = []
    effective_drain = drain or phase
    ra = (
        build_required_artifacts(policy_bundle.artifacts).get(effective_drain)
        if policy_bundle is not None
        else None
    )
    if ra is not None:
        paths.append(ra.artifact_path)
        if ra.markdown_path is not None:
            paths.append(ra.markdown_path)
    if policy_bundle is not None:
        phase_def = policy_bundle.pipeline.phases.get(phase)
        if phase_def is not None:
            if phase_def.parallelization is not None:
                paths.append(".agent/artifacts/parallel_development_summary.md")
            if phase_def.role == "commit" and ra is None:
                paths.append(COMMIT_MESSAGE_ARTIFACT)
    return tuple(paths)


def default_mcp_capabilities_for_phase(
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
) -> set[str]:
    """Return the default MCP capability set for a given phase."""
    return set(
        build_session_mcp_plan(
            transport=None,
            drain=phase,
            workspace_path=None,
            agents_policy=agents_policy,
        ).capabilities
    )
