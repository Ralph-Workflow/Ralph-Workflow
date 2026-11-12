from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    resolve_phase_required_artifact,
)

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy, PolicyBundle
    from ralph.workspace import FsWorkspace


def phase_output_artifact_paths(
    phase: str, *, drain: str | None = None, policy_bundle: PolicyBundle | None = None
) -> tuple[str, ...]:
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
    return set(
        build_session_mcp_plan(
            transport=None,
            drain=phase,
            workspace_path=None,
            agents_policy=agents_policy,
        ).capabilities
    )


def clear_phase_output_artifacts(
    workspace: FsWorkspace,
    phase: str,
    **opts: object,
) -> None:
    drain = cast("str | None", opts.get("drain"))
    policy_bundle = cast("PolicyBundle | None", opts.get("policy_bundle"))
    effective_drain = drain or phase
    required_artifact = (
        resolve_phase_required_artifact(
            policy_bundle.pipeline,
            policy_bundle.artifacts,
            phase=phase,
            drain=effective_drain,
        )
        if policy_bundle is not None
        else None
    )
    if required_artifact is not None and required_artifact.artifact_type == "plan":
        return
    for path in phase_output_artifact_paths(phase, drain=drain, policy_bundle=policy_bundle):
        workspace.remove(path)
