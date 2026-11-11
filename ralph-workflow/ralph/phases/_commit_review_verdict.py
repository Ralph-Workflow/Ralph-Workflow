"""Verdict validation, tree binding, and no-progress accounting for commit review.

A commit-review verdict is only meaningful when it is bound to the tree it
judged. This module owns that binding: it validates the verdict, decides
whether the artifact names the sealed proposal tree, and owns the
no-progress counter that bounds a reviewer malfunction instead of letting it
re-review forever.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.phases._commit_review_catalog import (
    COMMIT_REVIEW_IDENTITY_COUNTER,
    DEFAULT_IDENTITY_MAX,
)
from ralph.phases._commit_review_events import (
    CommitReviewEvent,
    CommitReviewGateFailed,
    CommitReviewLoopback,
)
from ralph.phases._commit_review_outcome import build_unapplied_retry_hint
from ralph.phases.required_artifacts import retry_hint_path
from ralph.policy.models._loop_counter_config import LoopCounterConfig
from ralph.policy.models._pipeline_policy import PipelinePolicy

if TYPE_CHECKING:
    from ralph.git.proposal_seal import Seal
    from ralph.phases import PhaseContext
    from ralph.phases._commit_review_actions import CommitReviewApplyReport
    from ralph.phases.commit_review import CommitReviewParsedArtifact
    from ralph.pipeline.events import Event

#: The workspace-relative file that holds the per-fingerprint no-progress
#: counters of the commit-review phase.
_IDENTITY_STATE_PATH = ".agent/tmp/commit_review_identity.json"

#: A sealed tree SHA is a 40-character hex string. A verdict that names
#: anything else is malformed and cannot be bound to a tree.
_TREE_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{40}$")

#: The no-progress state keeps one count per fingerprint, so two different
#: reviewer malfunctions cannot reset each other's budget. The cap keeps the
#: state file bounded when a reviewer submits a stream of distinct artifacts.
_MAX_IDENTITY_FINGERPRINTS: Final[int] = 32


def _verdict_failure(artifact: CommitReviewParsedArtifact) -> CommitReviewGateFailed | None:
    """Return the gate failure for an unusable verdict, or ``None``.

    The verdict is the only way to approve a proposal, so an artifact whose
    verdict is absent or outside the closed vocabulary fails the gate
    instead of being read as an approval.
    """
    verdict = artifact.verdict
    if verdict is None:
        return CommitReviewGateFailed(
            detail="Missing 'verdict' in commit_review artifact; must be 'approve' or 'reject'"
        )
    if verdict not in ("approve", "reject"):
        return CommitReviewGateFailed(
            detail=f"Invalid verdict {verdict!r}, must be 'approve' or 'reject'"
        )
    return None


def _tree_binding_fault(
    artifact: CommitReviewParsedArtifact,
    seal: Seal,
) -> str | None:
    """Return why the verdict is not bound to the sealed tree, or ``None``.

    A verdict is bound to a tree when it names the tree it judged. Three
    faults break that binding, and every one of them is a REVIEWER
    MALFUNCTION:

    * ``absent``: the artifact carries no ``tree_sha`` at all.
    * ``invalid``: the ``tree_sha`` is not a tree SHA.
    * ``stale``: the ``tree_sha`` names a different tree than the sealed one.

    A malfunction must never approve a tree the reviewer did not judge, and
    it must never roll the proposal back: the engine cannot tell WHICH tree
    the reviewer approved, so it can neither commit it nor discard it.
    """
    tree_sha = artifact.tree_sha
    if tree_sha is None or not tree_sha.strip():
        return "absent"
    if not _TREE_SHA_PATTERN.fullmatch(tree_sha.strip()):
        return "invalid"
    if tree_sha.strip() != seal.tree_sha:
        return "stale"
    return None


def _tree_binding_hint(
    fault: str,
    artifact: CommitReviewParsedArtifact,
    seal: Seal,
) -> str:
    """Return the retry hint that names the unbound verdict and the tree to judge."""
    if fault == "stale":
        return (
            f"Stale commit review: artifact tree SHA {artifact.tree_sha!r} "
            f"does not match current sealed proposal tree SHA {seal.tree_sha!r}; "
            "re-review the current tree."
        )
    if fault == "invalid":
        return (
            f"Invalid commit review: artifact tree SHA {artifact.tree_sha!r} is not a "
            "40-character hex tree SHA; re-review the current tree and submit the "
            f"artifact with tree_sha: {seal.tree_sha}."
        )
    return (
        "Unbound commit review: the artifact carries no 'tree_sha', so the verdict names "
        "no tree it judged; re-review the current tree and submit the artifact with "
        f"tree_sha: {seal.tree_sha}."
    )


def _tree_binding_events(
    ctx: PhaseContext,
    phase_name: str,
    fault: str,
    artifact: CommitReviewParsedArtifact,
    seal: Seal,
) -> list[Event | CommitReviewEvent]:
    """Return the events for a verdict that is not bound to the sealed tree.

    The malfunction is bounded by the phase-owned no-progress counter: the
    reviewer is sent back for a re-review, and a reviewer that never binds
    a verdict to the tree exhausts the budget and fails the gate. The gate
    failure is fail-closed: the pending work stays in the worktree and the
    proposal stays recoverable under its ref.
    """
    fingerprint = f"tree-binding:{fault}:{seal.tree_sha}"
    if charge_identity(ctx, phase_name, fingerprint):
        detail = (
            "Commit review verdict stayed unbound to the sealed proposal tree after the "
            f"identical no-progress bound ({fingerprint}); treating it as a reviewer "
            "malfunction"
        )
        logger.error("{}: {}", phase_name, detail)
        return [CommitReviewGateFailed(detail=detail)]
    hint = _tree_binding_hint(fault, artifact, seal)
    logger.warning("{}: {}", phase_name, hint)
    return [CommitReviewLoopback(hint=hint)]


def _rebind_verdict_to_sealed_tree(
    artifact: CommitReviewParsedArtifact,
    seal: Seal,
) -> CommitReviewParsedArtifact:
    """Re-bind the verdict to the tree the edit step produced.

    The artifact records the tree SHA it JUDGED. The edit step then applies
    the reviewer's own actions, which produce a NEW sealed tree. The verdict
    is evaluated against that new tree, so the binding is re-pointed at it:
    the approval names the tree that will be committed, and the commit phase
    still refuses to commit a tree that differs from it.
    """
    return replace(artifact, tree_sha=seal.tree_sha)


def maybe_succeed_after_identity_bound(
    ctx: PhaseContext,
    phase_name: str,
    *,
    fingerprint: str,
    events: list[Event | CommitReviewEvent],
) -> list[Event | CommitReviewEvent]:
    """Cap identical recoverable failures, then fail the gate.

    The identity bound stops a malfunctioning reviewer from looping forever.
    Reaching the bound is a reviewer malfunction -- a missing or invalid
    artifact -- so it is a gate failure, never an approval: the phase must
    not hand the commit phase an unbound (SHA-less) tree to commit.
    """
    if charge_identity(ctx, phase_name, fingerprint):
        logger.warning(
            "{}: identical no-progress bound reached; failing the commit review gate",
            phase_name,
        )
        return [
            CommitReviewGateFailed(
                detail=(
                    f"Commit review artifact missing or invalid after the identical "
                    f"no-progress bound ({fingerprint}); treating it as a reviewer malfunction"
                )
            )
        ]
    return events


def artifact_digest(ctx: PhaseContext, artifact_path: str) -> str:
    """Fingerprint the leftover or newly submitted commit-review artifact bytes."""
    if not ctx.workspace.exists(artifact_path):
        return "missing"
    try:
        raw = ctx.workspace.read(artifact_path)
    except Exception:
        return "unreadable"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def charge_identity(ctx: PhaseContext, phase_name: str, fingerprint: str) -> bool:
    """Increment the phase-owned identity counter; return True when the cap is reached.

    Every fingerprint keeps its OWN count, so two different reviewer
    malfunctions cannot reset each other's budget: a reviewer that
    alternates between two kinds of malfunction still exhausts the budget
    of the one it repeats.
    """
    counts = _read_identity_counts(ctx)
    key = f"{phase_name}|{fingerprint}"
    count = counts.get(key, 0) + 1
    counts[key] = count
    _write_identity_counts(ctx, counts)
    return count >= _identity_max(ctx)


def _identity_max(ctx: PhaseContext) -> int:
    """Return the policy cap for identical no-progress commit-review attempts."""
    policy: object = ctx.pipeline_policy
    if isinstance(policy, PipelinePolicy):
        cfg = policy.loop_counters.get(COMMIT_REVIEW_IDENTITY_COUNTER)
        parsed = DEFAULT_IDENTITY_MAX if cfg is None else cfg.default_max
    else:
        parsed = _identity_max_from_untyped_policy(policy)
    if parsed <= 0:
        return DEFAULT_IDENTITY_MAX
    return parsed


def _identity_max_from_untyped_policy(policy: object) -> int:
    """Read the identity cap from test doubles that are not PipelinePolicy."""
    counters = cast("object", getattr(policy, "loop_counters", None))
    if not isinstance(counters, dict):
        return DEFAULT_IDENTITY_MAX
    cfg_obj = cast("object", counters.get(COMMIT_REVIEW_IDENTITY_COUNTER))
    if cfg_obj is None:
        return DEFAULT_IDENTITY_MAX
    if isinstance(cfg_obj, LoopCounterConfig):
        return cfg_obj.default_max
    raw = cast("object", getattr(cfg_obj, "default_max", DEFAULT_IDENTITY_MAX))
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_IDENTITY_MAX
    return raw


def _read_identity_counts(ctx: PhaseContext) -> dict[str, int]:
    """Return the per-fingerprint no-progress counts of this phase."""
    if not ctx.workspace.exists(_IDENTITY_STATE_PATH):
        return {}
    try:
        loaded: object = json.loads(ctx.workspace.read(_IDENTITY_STATE_PATH))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    raw = loaded.get("counts")
    if not isinstance(raw, dict):
        return {}
    counts: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        counts[key] = value
    return counts


def _write_identity_counts(ctx: PhaseContext, counts: dict[str, int]) -> None:
    """Persist the per-fingerprint no-progress counts of this phase."""
    retained = dict(list(counts.items())[-_MAX_IDENTITY_FINGERPRINTS:])
    payload = {"counts": retained}
    try:
        ctx.workspace.write(_IDENTITY_STATE_PATH, json.dumps(payload))
    except Exception as exc:
        logger.warning("Failed to persist commit-review identity state: {}", exc)


def persist_unapplied_hint(
    ctx: PhaseContext,
    phase_name: str,
    report: CommitReviewApplyReport,
) -> None:
    """Write declined and apply-failed decisions for the next prompt."""
    hint = build_unapplied_retry_hint(report)
    if not hint:
        return
    try:
        ctx.workspace.write(
            retry_hint_path(
                phase_name,
                pipeline_policy=ctx.pipeline_policy
                if isinstance(ctx.pipeline_policy, PipelinePolicy)
                else None,
            ),
            hint,
        )
    except Exception as exc:
        logger.warning("Failed to persist commit-review retry hint: {}", exc)


__all__ = [
    "artifact_digest",
    "charge_identity",
    "maybe_succeed_after_identity_bound",
    "persist_unapplied_hint",
]
