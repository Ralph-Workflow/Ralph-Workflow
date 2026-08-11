"""Tier selection for deterministic and on-demand visual review."""

from __future__ import annotations

from enum import StrEnum

from ralph.agents.vision_agent_provisioning import dispatch_vision_verdict
from ralph.visual.on_demand_capture_evidence import OnDemandCaptureEvidence
from ralph.visual.on_demand_judgement_deps import OnDemandJudgementDeps
from ralph.visual.on_demand_judgement_result import OnDemandJudgementResult


class JudgementTier(StrEnum):
    """The visual-review execution tier and its bounded data contracts."""

    DETERMINISTIC = "deterministic"
    ON_DEMAND = "on-demand"

    @property
    def is_blocking(self) -> bool:
        """Return whether this tier belongs in the verification gate."""
        return self is JudgementTier.DETERMINISTIC


def run_on_demand_judgement(
    target: str,
    intent: str,
    *,
    deps: OnDemandJudgementDeps | None = None,
) -> OnDemandJudgementResult:
    """Submit a non-blocking, ledger-attributed vision verdict through bounded seams."""
    if not target.strip() or not intent.strip():
        return OnDemandJudgementResult(blocker="target and design intent are required")
    if deps is None:
        return OnDemandJudgementResult(
            blocker="capture or vision delegation unavailable: start a managed Ralph MCP session"
        )
    before_handles = deps.load_retained_capture(target)
    if not before_handles:
        return OnDemandJudgementResult(blocker="retained capture evidence is unavailable")
    after_handles = deps.load_fresh_capture(target)
    if not after_handles:
        return OnDemandJudgementResult(blocker="fresh capture evidence is unavailable")
    delegated_agent_id = deps.delegated_agent_id()
    if delegated_agent_id is None or not delegated_agent_id.strip():
        return OnDemandJudgementResult(blocker="vision delegation is unavailable")
    evidence = OnDemandCaptureEvidence(before_handles, after_handles)
    try:
        verdict_id = dispatch_vision_verdict(
            target=target,
            intent=intent,
            before_handles=evidence.before_handles,
            after_handles=evidence.after_handles,
            delegated_agent_id=delegated_agent_id,
            timeout_seconds=deps.timeout_seconds,
            invoke=deps.invoke_vision,
        )
    except ValueError as exc:
        return OnDemandJudgementResult(blocker=f"vision delegation failed: {exc}")
    if not deps.validate_submission(verdict_id, evidence, delegated_agent_id):
        return OnDemandJudgementResult(
            blocker="submitted design verdict lacks delegated-agent capture evidence"
        )
    return OnDemandJudgementResult(verdict_id=verdict_id, status="submitted")


def resolve_judgement_tier(value: str | None) -> JudgementTier:
    """Resolve an explicit tier, defaulting verification to deterministic."""
    if value is None:
        return JudgementTier.DETERMINISTIC
    return JudgementTier(value)


__all__ = [
    "JudgementTier",
    "OnDemandCaptureEvidence",
    "OnDemandJudgementDeps",
    "OnDemandJudgementResult",
    "resolve_judgement_tier",
    "run_on_demand_judgement",
]
