"""PhaseDefinition Pydantic model."""

from typing import Literal, cast

from pydantic import Field, model_validator

from ralph.policy.models._artifact_history_policy import ArtifactHistoryPolicy
from ralph.policy.models._artifact_proof_policy import ArtifactProofPolicy
from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel
from ralph.policy.models._phase_commit_policy import PhaseCommitPolicy
from ralph.policy.models._phase_decision_route import PhaseDecisionRoute
from ralph.policy.models._phase_loop_policy import PhaseLoopPolicy
from ralph.policy.models._phase_parallelization import PhaseParallelization
from ralph.policy.models._phase_retry_policy import PhaseRetryPolicy
from ralph.policy.models._phase_transition import PhaseTransition
from ralph.policy.models._phase_verification_policy import PhaseVerificationPolicy
from ralph.policy.models._phase_workflow_fallback import PhaseWorkflowFallback
from ralph.policy.models._types import PhaseRole


class PhaseDefinition(_FrozenPolicyModel):
    """Definition of a single phase in the pipeline graph."""

    drain: str = Field(..., description="Drain binding for this phase")
    transitions: PhaseTransition = Field(..., description="Transition routing rules")

    role: PhaseRole | None = Field(
        default=None,
        description="Phase role classifying behavior contract",
    )
    skip_invocation: bool = Field(
        default=False,
        description=(
            "When True, the runtime routes directly without invoking an agent. "
            "Useful for pass-through or routing-only phases."
        ),
    )
    retry_policy: PhaseRetryPolicy | None = Field(
        default=None,
        description="Per-phase retry policy override",
    )
    loop_policy: PhaseLoopPolicy | None = Field(
        default=None,
        description="Loop bounds for analysis phases",
    )
    decisions: dict[str, PhaseDecisionRoute] = Field(
        default_factory=dict,
        description="Analysis decision routing map",
    )
    commit_policy: PhaseCommitPolicy | None = Field(
        default=None,
        description="Commit semantics for commit phases",
    )
    verification: PhaseVerificationPolicy | None = Field(
        default=None,
        description="Verification gating policy",
    )
    artifact_required: bool = Field(
        default=True,
        description=(
            "Whether this phase's output artifact is required for phase success when "
            "the phase's drain has an artifact contract. Defaults to True."
        ),
    )
    terminal_outcome: Literal["success", "failure"] | None = Field(
        default=None,
        description="Explicit terminal outcome declaration",
    )
    bypass_routes: dict[str, str] = Field(
        default_factory=dict,
        description="Named bypass routes (outcome -> target phase)",
    )
    result_status_post_commit: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Execution artifact status routes applied after the next commit outcome. "
            "Statuses absent from this map follow the phase's normal analyzer flow."
        ),
    )
    clean_outcome: str | None = Field(
        default=None,
        description=(
            "For role='review': the bypass_routes key that signals a clean review. "
            "The reducer looks up this key in bypass_routes to find the target phase. "
            "Required when role='review' and bypass_routes is non-empty."
        ),
    )
    issues_outcome: str | None = Field(
        default=None,
        description=(
            "For role='review': the value to set as review_outcome when issues are found. "
            "Required when role='review'. Drives review_outcome propagation downstream."
        ),
    )
    prompt_template: str | None = Field(
        default=None,
        description="File-backed .jinja prompt template for this phase",
    )
    continuation_template: str | None = Field(
        default=None,
        description="Optional continuation .jinja prompt template for this phase",
    )
    loopback_prompt_template: str | None = Field(
        default=None,
        description=(
            "Optional alternate .jinja prompt template for loopback retries that "
            "need structured upstream feedback."
        ),
    )
    parallelization: PhaseParallelization | None = Field(
        default=None,
        description=(
            "Transition-scoped parallelization policy. When None, multi-work-unit plans "
            "must not fan out from this phase."
        ),
    )
    artifact_history: ArtifactHistoryPolicy | None = Field(
        default=None,
        description=(
            "Optional artifact history policy. When set with enabled=True, the runtime "
            "archives the prior canonical artifact and Markdown handoff before overwrite. "
            "Phases sharing the same drain must agree on artifact_history.enabled."
        ),
    )
    artifact_proof_policy: ArtifactProofPolicy | None = Field(
        default=None,
        description=(
            "Optional proof-validation policy for development_result artifacts. When set, "
            "the runtime validates plan and analysis proof entries before accepting the "
            "development_result artifact."
        ),
    )
    workflow_fallback: PhaseWorkflowFallback | None = Field(
        default=None,
        description=(
            "Policy-declared fallback route when this phase's agent chain is exhausted. "
            "When set, routes to target instead of the global recovery.failed_route. "
            "Takes precedence over recovery.failed_route on chain exhaustion."
        ),
    )
    clear_drains_on_fresh_entry: list[str] = Field(
        default_factory=list,
        description=(
            "Drain names to clear on every genuine fresh phase entry. "
            "On fresh entry (program start, cross-phase transition, or last-commit re-entry), "
            "Ralph Workflow deletes the primary artifact JSON and Markdown handoff for each "
            "listed drain. Empty list means no drain-based clearing on entry. "
            "Contrast with artifact_history.clear_on_fresh_entry which clears only the history."
        ),
    )
    display_style: str | None = Field(
        default=None,
        description=(
            "Per-phase rich style override for phase banners. "
            "When set, this style string is used instead of the role-based default in "
            "phase_banner.phase_style. For example, set to 'theme.phase.planning' to give "
            "the planning phase a distinct color from other execution-role phases. "
            "Available theme keys include theme.phase.planning, theme.phase.development, "
            "theme.phase.development_analysis, theme.phase.commit, and theme.phase.failed."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d: dict[str, object] = cast("dict[str, object]", data)
        if d.get("embeds_analysis"):
            raise ValueError(
                "PhaseDefinition.embeds_analysis has been removed. "
                "Set role='analysis' instead. "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        if d.get("requires_commit"):
            raise ValueError(
                "PhaseDefinition.requires_commit has been removed. "
                "Set role='commit' instead. "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        return d
