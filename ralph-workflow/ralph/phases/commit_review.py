"""Commit review phase handler.

Orchestrates the two-stage safety gate and edit phase before commit:

* Stage 1: Deterministic tripwires on a sealed Git proposal tree.
* Stage 2: Agent review evaluating proposed diff and executing safe housekeeping.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from git import InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.git.commit_review import (
    add_to_git_exclude,
    delete_file_from_repo,
    ensure_git_initialized,
    untrack_engine_internal_files,
)
from ralph.git.operations import append_to_gitignore
from ralph.git.proposal_seal import DiffEntry, Seal, diff_tree, seal_pending
from ralph.mcp.artifacts._commit_review_action import CommitReviewAction
from ralph.mcp.artifacts.citation_verifier import verify_reject_citations
from ralph.mcp.artifacts.markdown import parse_markdown_document
from ralph.phases._agent_internal_paths import is_agent_internal_path
from ralph.phases._commit_review_actions import (
    CommitReviewApplyReport,
    _CommitReviewLike,
    apply_commit_review_actions,
)
from ralph.phases._commit_review_catalog import (
    GENERATED_TEXT_DIRECTORIES as _GENERATED_TEXT_DIRECTORIES,
)
from ralph.phases._commit_review_catalog import (
    GENERATED_TEXT_EXTENSIONS,
    LOCKFILE_BASENAMES,
    TEMPORARY_SUFFIXES,
)
from ralph.phases._commit_review_catalog import (
    GENERATED_TEXT_MARKERS as _GENERATED_TEXT_MARKERS,
)
from ralph.phases._commit_review_catalog import (
    HOUSEKEEPING_BASENAMES as _HOUSEKEEPING_BASENAMES,
)
from ralph.phases._commit_review_catalog import (
    PROTECTED_BASENAMES as _PROTECTED_BASENAMES,
)
from ralph.phases._commit_review_catalog import (
    SOURCE_FILE_GENERATED_MARKERS as _SOURCE_FILE_GENERATED_MARKERS,
)
from ralph.phases._commit_review_catalog import (
    UNSAFE_EXTENSIONS as _UNSAFE_EXTENSIONS,
)
from ralph.phases._commit_review_catalog import (
    UNSAFE_PATH_SEGMENTS as _UNSAFE_PATH_SEGMENTS,
)
from ralph.phases._commit_review_events import (
    CommitReviewApproved,
    CommitReviewEvent,
    CommitReviewGateFailed,
    CommitReviewLoopback,
    CommitReviewRejected,
)
from ralph.phases._commit_review_outcome import (
    build_commit_review_retry_hint,
    decide_commit_review_outcome,
)
from ralph.phases._commit_review_tripwires import (
    DEFAULT_BINARY_SIZE_LIMIT,
    DEFAULT_MAX_DELETED_FILES,
    DEFAULT_MAX_DELETED_FRACTION,
    Finding,
    TripwireReport,
    TripwireThresholds,
    evaluate_tripwires,
    resolve_immutable_tracked_count,
)
from ralph.phases._commit_review_verdict import (
    _rebind_verdict_to_sealed_tree,
    _tree_binding_events,
    _tree_binding_fault,
    _verdict_failure,
    artifact_digest,
    maybe_succeed_after_identity_bound,
    persist_unapplied_hint,
)
from ralph.phases.required_artifacts import clear_validation_retry_hint
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.policy.models._pipeline_policy import PipelinePolicy
from ralph.recovery.classifier import FailureCategory

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection
    from ralph.phases import PhaseContext

COMMIT_REVIEW_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"
_IDENTITY_STATE_PATH = ".agent/tmp/commit_review_identity.json"
_COMMIT_REVIEW_CONTEXT_PATH = ".agent/tmp/commit_review_context.json"

#: The soft tripwire categories that must NOT survive the commit-review edit
#: step. A staged secret and an oversized binary are FIRST offered to the edit
#: step (the reviewer may untrack or ignore them); when the unsafe content is
#: still in the re-sealed proposal, the engine rejects on its own evidence.
#: The out-of-scope signal stays advisory and the hard tripwires keep their
#: own Stage 1 behaviour.
_UNSAFE_SURVIVAL_CATEGORIES: Final[frozenset[str]] = frozenset({"secret", "binary_size"})

#: A sealed tree SHA is a 40-character hex string. A verdict that names
#: anything else is malformed and cannot be bound to a tree.
_TREE_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{40}$")

#: The no-progress state keeps one count per fingerprint, so two different
#: reviewer malfunctions cannot reset each other's budget. The cap keeps the
#: state file bounded when a reviewer submits a stream of distinct artifacts.
_MAX_IDENTITY_FINGERPRINTS: Final[int] = 32


def _close_repo(repo: Repo | None) -> None:
    close = cast("Callable[[], object] | None", getattr(repo, "close", None))
    if callable(close):
        close()


def _path_exists_in_head(repo_root: Path, relative_path: str) -> bool:
    """Return True when ``relative_path`` already exists in HEAD."""
    repo: Repo | None = None
    try:
        repo = Repo(repo_root, search_parent_directories=False)
        try:
            repo.git.cat_file("-e", f"HEAD:{relative_path}")
            return True
        except Exception:
            return False
    except InvalidGitRepositoryError:
        return False
    finally:
        _close_repo(repo)


def _is_generated_text_artifact(
    repo_root: Path,
    path: str,
    markers: frozenset[str] = _GENERATED_TEXT_MARKERS,
) -> bool:
    """Return True when ``path`` looks like a generated artifact, not authored content."""
    candidate = Path(path)
    name_tokens = {
        token
        for token in candidate.stem.lower().replace(".", "-").replace("_", "-").split("-")
        if token
    }
    parent_parts = {part.lower() for part in candidate.parts[:-1]}
    has_generated_signal = bool(name_tokens & markers) or bool(
        parent_parts & _GENERATED_TEXT_DIRECTORIES
    )
    if not has_generated_signal:
        return False
    return not _path_exists_in_head(repo_root, path)


def _is_safe_to_delete(repo_root: Path, path: str) -> bool:
    """Return True only if path is a housekeeping artifact safe to delete.

    The check order matters:
    1. The agent-internal fast path (FIRST statement in the function body).
    2. Protected basenames win over suffix-based rules.
    3. Housekeeping basenames win over the unsafe-extension fall-through.
    4. Paths with parent-traversal segments or absolute paths are rejected.
    """
    if is_agent_internal_path(path):
        return True
    candidate = Path(path)
    path_lower = path.lower()
    suffix = candidate.suffix.lower()
    if _is_protected_path(repo_root, candidate, path_lower):
        return False
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return False
    return _is_deletable_housekeeping(repo_root, candidate, suffix)


def _is_protected_path(
    repo_root: Path,
    candidate: Path,
    path_lower: str,
) -> bool:
    """Return True for paths that must never be deleted, regardless of suffix."""
    if any(seg in path_lower for seg in _UNSAFE_PATH_SEGMENTS):
        return True
    if candidate.name in LOCKFILE_BASENAMES:
        return True
    if candidate.name.lower() in _PROTECTED_BASENAMES:
        return True
    return candidate.name in _HOUSEKEEPING_BASENAMES and _path_exists_in_head(
        repo_root, str(candidate)
    )


def _is_deletable_housekeeping(
    repo_root: Path,
    candidate: Path,
    suffix: str,
) -> bool:
    """Return True for files that are safe housekeeping artifacts to delete."""
    if candidate.name in _HOUSEKEEPING_BASENAMES:
        return not _path_exists_in_head(repo_root, str(candidate))
    if suffix in TEMPORARY_SUFFIXES:
        return not _path_exists_in_head(repo_root, str(candidate))
    if suffix in GENERATED_TEXT_EXTENSIONS:
        return _is_generated_text_artifact(repo_root, str(candidate))
    if _is_generated_text_artifact(
        repo_root, str(candidate), markers=_SOURCE_FILE_GENERATED_MARKERS
    ):
        return True
    return suffix not in _UNSAFE_EXTENSIONS


def _resolve_run_id_and_iteration(
    ctx: PhaseContext,
    effect: Effect,
) -> tuple[str, str]:
    """Extract run_id and iteration from context/effect or fallback to defaults."""
    run_id_val = cast("object", getattr(ctx, "run_id", None))
    if not isinstance(run_id_val, str) or not run_id_val:
        run_id_val = cast("object", getattr(effect, "run_id", None))
    if not isinstance(run_id_val, str) or not run_id_val:
        run_id_val = "run"

    iteration_val = cast("object", getattr(effect, "iteration", None))
    if not isinstance(iteration_val, str) or not iteration_val:
        iteration_val = cast("object", getattr(ctx, "iteration", None))
    if not isinstance(iteration_val, str) or not iteration_val:
        iteration_val = "1"

    return str(run_id_val), str(iteration_val)


def _resolve_diff_base(repo_root: Path) -> str:
    """Resolve the base ref for diff-tree, defaulting to empty tree if HEAD cannot be resolved."""
    repo: Repo | None = None
    try:
        repo = Repo(repo_root, search_parent_directories=False)
        repo.git.rev_parse("--verify", "HEAD")
        return "HEAD"
    except Exception:
        return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    finally:
        _close_repo(repo)


def _resolve_policy_thresholds(
    ctx_or_policy: object,
    phase_name: str,
) -> tuple[TripwireThresholds, list[str] | None, list[str] | None]:
    """Read tripwire thresholds and path filters from pipeline policy."""
    thresholds = TripwireThresholds()
    allowed_dirs: list[str] | None = None
    protected: list[str] | None = None

    policy: object = getattr(ctx_or_policy, "pipeline_policy", ctx_or_policy)
    if isinstance(policy, PipelinePolicy):
        cr_policy_obj = cast("object", getattr(policy, "commit_review", None))
        if cr_policy_obj is not None:
            max_del_obj = cast(
                "object", getattr(cr_policy_obj, "max_deleted_files", DEFAULT_MAX_DELETED_FILES)
            )
            max_frac_obj = cast(
                "object",
                getattr(cr_policy_obj, "max_deleted_fraction", DEFAULT_MAX_DELETED_FRACTION),
            )
            bin_size_obj = cast(
                "object", getattr(cr_policy_obj, "binary_size_limit", DEFAULT_BINARY_SIZE_LIMIT)
            )
            max_del = max_del_obj if isinstance(max_del_obj, int) else DEFAULT_MAX_DELETED_FILES
            max_frac = (
                max_frac_obj if isinstance(max_frac_obj, float) else DEFAULT_MAX_DELETED_FRACTION
            )
            bin_size = bin_size_obj if isinstance(bin_size_obj, int) else DEFAULT_BINARY_SIZE_LIMIT
            thresholds = TripwireThresholds(
                max_deleted_files=max_del,
                max_deleted_fraction=max_frac,
                binary_size_limit=bin_size,
            )
        phase_def = policy.phases.get(phase_name)
        if phase_def is not None:
            ad_obj = cast("object", getattr(phase_def, "allowed_directories", None))
            if isinstance(ad_obj, (list, tuple, set)):
                allowed_dirs = [str(d) for d in ad_obj]
            pp_obj = cast("object", getattr(phase_def, "protected_paths", None))
            if isinstance(pp_obj, (list, tuple, set)):
                protected = [str(p) for p in pp_obj]

    return thresholds, allowed_dirs, protected


def _persist_commit_review_context_to_root(
    repo_root: Path,
    seal: Seal,
    soft_findings: tuple[Finding, ...],
    diff_entries: list[DiffEntry],
) -> None:
    """Provide proposal ref, soft tripwires, and diff evidence to agent context."""
    payload = {
        "proposal_ref": seal.ref,
        "tree_sha": seal.tree_sha,
        "soft_tripwires": [
            {"category": f.category, "message": f.message, "path": f.path, "count": f.count}
            for f in soft_findings
        ],
        "diff_entries": [
            {
                "path": e.path,
                "status": e.status,
                "size": e.size,
                "is_binary": e.is_binary,
            }
            for e in diff_entries
        ],
    }
    try:
        from ralph.workspace.fs import FsWorkspace

        ws = FsWorkspace(repo_root)
        ws.write(_COMMIT_REVIEW_CONTEXT_PATH, json.dumps(payload, indent=2))
    except Exception as exc:
        logger.debug("Could not persist commit review context: {}", exc)


def _persist_commit_review_context(
    ctx: PhaseContext,
    seal: Seal,
    soft_findings: tuple[Finding, ...],
    diff_entries: list[DiffEntry],
) -> None:
    """Provide proposal ref, soft tripwires, and diff evidence to agent context."""
    repo_root, _ = _resolve_workspace_root_safe(ctx)
    if repo_root is not None:
        _persist_commit_review_context_to_root(repo_root, seal, soft_findings, diff_entries)


@dataclass
class CommitReviewParsedArtifact:
    """Parsed commit review artifact."""

    verdict: str | None
    tree_sha: str | None
    actions: list[CommitReviewAction] = field(default_factory=list)
    reason: str | None = None
    reason_items: list[str] = field(default_factory=list)


def _parse_action_line(text: str) -> CommitReviewAction | None:
    line = text.strip()
    if line.startswith(("-", "*")):
        line = line.lstrip("-* \t")
    if line.startswith("[") and "]" in line:
        line = line.split("]", 1)[1].strip()
    action, sep, value = line.partition(" | ")
    if not sep or not action or not value:
        return None
    action = action.strip()
    value = value.strip()
    try:
        if action == "delete_file":
            return CommitReviewAction.model_validate({"action": action, "path": value})
        if action in ("add_to_gitignore", "add_to_git_exclude"):
            return CommitReviewAction.model_validate({"action": action, "pattern": value})
        return None
    except Exception:
        return None


def _read_commit_review_document(
    ctx: PhaseContext,
    phase_name: str,
) -> ParsedDocument | None:
    """Read and validate the basic frontmatter of the commit_review Markdown artifact."""
    if not ctx.workspace.exists(COMMIT_REVIEW_ARTIFACT_PATH):
        logger.warning(
            "{}: missing commit_review artifact at {}",
            phase_name,
            COMMIT_REVIEW_ARTIFACT_PATH,
        )
        return None

    try:
        text = ctx.workspace.read(COMMIT_REVIEW_ARTIFACT_PATH)
        document, _ = parse_markdown_document(text)
    except Exception as exc:
        logger.warning("{}: failed to parse markdown artifact: {}", phase_name, exc)
        return None

    if document.frontmatter.get("type") != "commit_review":
        logger.warning(
            "{}: artifact frontmatter type {!r} is not 'commit_review'",
            phase_name,
            document.frontmatter.get("type"),
        )
        return None

    return document


def _parse_actions_section(
    actions_sec: ParsedSection | None,
    phase_name: str,
) -> list[CommitReviewAction] | None:
    if actions_sec is None:
        return []
    if any(line.text.strip() for line in actions_sec.lines):
        logger.warning(
            "{}: Actions section contains unparseable non-item lines",
            phase_name,
        )
        return None
    actions: list[CommitReviewAction] = []
    for item in actions_sec.items:
        act = _parse_action_line(item.text)
        if act is None:
            logger.warning(
                "{}: Actions section contains unparseable action line: {!r}",
                phase_name,
                item.text,
            )
            return None
        actions.append(act)
    return actions


def _load_commit_review_artifact(
    ctx: PhaseContext,
    phase_name: str,
) -> CommitReviewParsedArtifact | None:
    """Load and parse the commit_review Markdown artifact."""
    document = _read_commit_review_document(ctx, phase_name)
    if document is None:
        return None

    verdict_raw = document.frontmatter.get("verdict")
    tree_sha_raw = document.frontmatter.get("tree_sha")

    verdict: str | None = None
    if verdict_raw is not None:
        verdict_str = str(verdict_raw).strip()
        if verdict_str.lower() in ("approve", "reject") and verdict_str not in (
            "approve",
            "reject",
        ):
            logger.warning(
                "{}: commit_review artifact has invalid casing for verdict {!r} (must be lowercase 'approve' or 'reject')",
                phase_name,
                verdict_raw,
            )
            return None
        verdict = verdict_str

    tree_sha: str | None = None
    if tree_sha_raw is not None:
        tree_sha = str(tree_sha_raw).strip()

    actions = _parse_actions_section(document.section("Actions"), phase_name)
    if actions is None:
        return None

    reason_items: list[str] = []
    reason_sec = document.section("Reason")
    if reason_sec is not None:
        reason_items = [i.text.strip() for i in reason_sec.items if i.text.strip()]
        if not reason_items:
            reason_items = [
                re.sub(r"^-\s*(\[[^\]]+\]\s*)?", "", line.text).strip()
                for line in reason_sec.lines
                if line.text.strip()
            ]

    reason_str: str | None = "\n".join(reason_items) if reason_items else None

    # Strict contract: a rejecting artifact bound to a tree requires ## Reason with list items.
    # Frontmatter reason is never accepted as a substitute for ## Reason.
    # If tree_sha is absent or invalid, tree binding loopback handles it.
    is_tree_bound = tree_sha is not None and bool(_TREE_SHA_PATTERN.fullmatch(tree_sha))
    if (
        verdict == "reject"
        and is_tree_bound
        and (reason_sec is None or not reason_items or not reason_str)
    ):
        logger.warning(
            "{}: rejecting commit_review artifact requires a non-empty '## Reason' section",
            phase_name,
        )
        return None

    return CommitReviewParsedArtifact(
        verdict=verdict,
        tree_sha=tree_sha,
        actions=actions,
        reason=reason_str,
        reason_items=reason_items,
    )


def _apply_commit_review_report(
    repo_root: Path,
    commit_review: CommitReviewParsedArtifact | _CommitReviewLike,
) -> CommitReviewApplyReport:
    """Apply actions through the isolated action engine."""
    return apply_commit_review_actions(
        repo_root,
        commit_review,
        is_safe_to_delete=_is_safe_to_delete,
        append_to_gitignore=append_to_gitignore,
        add_to_git_exclude=add_to_git_exclude,
        delete_file_from_repo=delete_file_from_repo,
    )


def _apply_commit_review_actions(
    repo_root: Path,
    commit_review: CommitReviewParsedArtifact | _CommitReviewLike,
) -> tuple[list[str], list[str]]:
    """Apply actions and return (declined, failed-delete) for existing tests."""
    report = _apply_commit_review_report(repo_root, commit_review)
    return report.declined_delete_paths, report.failed_delete_paths


def run_commit_review_stage1_preflight(
    repo_root: Path,
    run_id: str,
    iteration: str,
    pipeline_policy: object,
    phase_name: str,
) -> (
    tuple[Seal, list[DiffEntry], TripwireReport, str]
    | CommitReviewRejected
    | CommitReviewGateFailed
):
    """Run Stage 1 deterministic tripwires on the sealed proposal tree before agent review."""
    try:
        ensure_git_initialized(str(repo_root))
    except Exception as exc:
        logger.warning("ensure_git_initialized failed: {}", exc)

    try:
        untrack_engine_internal_files(repo_root, is_agent_internal_path)
    except Exception as exc:
        logger.warning("untrack_engine_internal_files failed: {}", exc)

    try:
        from ralph.config.bootstrap import auto_seed_default_gitignore

        auto_seed_default_gitignore(repo_root)
    except Exception as exc:
        logger.warning("auto_seed_default_gitignore failed: {}", exc)

    try:
        from ralph.config.bootstrap import auto_seed_default_git_exclude

        auto_seed_default_git_exclude(repo_root)
    except Exception as exc:
        logger.warning("auto_seed_default_git_exclude failed: {}", exc)

    try:
        seal = seal_pending(repo_root, run_id, iteration)
    except Exception as exc:
        logger.error("{}: failed to seal proposal tree: {}", phase_name, exc)
        return CommitReviewGateFailed(detail=f"Failed to seal proposal tree: {exc}")

    base = _resolve_diff_base(repo_root)
    try:
        diff_entries = diff_tree(repo_root, seal.ref, base=base)
    except Exception as exc:
        logger.error("{}: failed to compute diff-tree: {}", phase_name, exc)
        return CommitReviewGateFailed(detail=f"Failed to compute diff-tree: {exc}")

    tracked_count = resolve_immutable_tracked_count(diff_entries)
    if tracked_count is None:
        detail = (
            "Mass deletion check unavailable: the immutable base-tree membership could "
            "not be read, so the deleted-file fraction cannot be measured against a "
            "trustworthy denominator. This is an inability to MEASURE, not measured "
            "damage: the proposal is NOT rejected, the worktree and the proposal ref "
            "are preserved, and the gate fails so an operator can restore the "
            "evidence source."
        )
        logger.error("{}: {}", phase_name, detail)
        return CommitReviewGateFailed(detail=detail)

    thresholds, allowed_dirs, protected = _resolve_policy_thresholds(pipeline_policy, phase_name)

    tripwire_entries = [
        e
        for e in diff_entries
        if not (
            e.path == ".agent" or e.path.startswith(".agent/") or is_agent_internal_path(e.path)
        )
    ]
    tripwire_report = evaluate_tripwires(
        tripwire_entries,
        tracked_count,
        thresholds,
        allowed_dirs=allowed_dirs,
        protected=protected,
    )

    _persist_commit_review_context_to_root(repo_root, seal, tripwire_report.soft, diff_entries)

    if tripwire_report.has_hard_tripwires:
        reason = "; ".join(f.message for f in tripwire_report.hard)
        logger.warning("{}: Stage 1 hard tripwires triggered: {}", phase_name, reason)
        return CommitReviewRejected(reason=reason, ref=seal.ref, hard=True)

    return seal, diff_entries, tripwire_report, base


def _run_stage1(
    repo_root: Path,
    run_id: str,
    iteration: str,
    ctx: PhaseContext,
    phase_name: str,
) -> tuple[Seal, list[DiffEntry], TripwireReport, str] | list[Event | CommitReviewEvent]:
    res = run_commit_review_stage1_preflight(
        repo_root,
        run_id,
        iteration,
        ctx.pipeline_policy,
        phase_name,
    )
    if isinstance(res, (CommitReviewRejected, CommitReviewGateFailed)):
        return [res]
    return res


def _handle_actions_and_reseal(
    repo_root: Path,
    artifact: CommitReviewParsedArtifact,
    seal: Seal,
    diff_entries: list[DiffEntry],
    run_id: str,
    iteration: str,
    base: str,
    phase_name: str,
    ctx: PhaseContext,
) -> tuple[Seal, list[DiffEntry]] | list[Event | CommitReviewEvent]:
    if not artifact.actions:
        return seal, diff_entries

    try:
        report = _apply_commit_review_report(repo_root, artifact)
    except Exception as exc:
        _consume_commit_review_artifact(ctx)
        return [CommitReviewGateFailed(detail=f"Failed to apply commit review actions: {exc}")]

    persist_unapplied_hint(ctx, phase_name, report)

    loopback = decide_commit_review_outcome(phase_name, report)
    if loopback is not None:
        _consume_commit_review_artifact(ctx)
        return [loopback]

    if report.applied_count > 0:
        try:
            seal = seal_pending(repo_root, run_id, iteration)
            diff_entries = diff_tree(repo_root, seal.ref, base=base)
        except Exception as exc:
            _consume_commit_review_artifact(ctx)
            return [CommitReviewGateFailed(detail=f"Failed to reseal proposal tree: {exc}")]

    return seal, diff_entries


def _surviving_unsafe_findings(
    diff_entries: list[DiffEntry],
    phase_name: str,
    ctx: PhaseContext,
) -> tuple[Finding, ...]:
    """Return the secret and oversized-binary findings that survive the edit step.

    The re-check runs on the diff of the RE-SEALED proposal tree, so a finding
    the edit step removed is absent and a finding the edit step could not
    remove is still there. Only the two unsafe categories are considered: the
    out-of-scope signal stays advisory and the hard tripwires keep their own
    Stage 1 behaviour.
    """
    thresholds, allowed_dirs, protected = _resolve_policy_thresholds(
        ctx.pipeline_policy, phase_name
    )
    entries = [
        entry
        for entry in diff_entries
        if not (
            entry.path == ".agent"
            or entry.path.startswith(".agent/")
            or is_agent_internal_path(entry.path)
        )
    ]
    tracked_count = resolve_immutable_tracked_count(entries) or 0
    report = evaluate_tripwires(
        entries,
        tracked_count,
        thresholds,
        allowed_dirs=allowed_dirs,
        protected=protected,
    )
    return tuple(
        finding for finding in report.soft if finding.category in _UNSAFE_SURVIVAL_CATEGORIES
    )


def _render_surviving_unsafe_reason(findings: tuple[Finding, ...]) -> str:
    """Render the engine-owned evidence for unsafe content that survived the edit step.

    The reason names the affected paths and the concrete counts and limits, so
    the rejection stands on the engine's own evidence and never depends on the
    reviewer's ``## Reason`` citations being verifiable.
    """
    rendered = []
    for finding in findings:
        location = finding.path or "<unknown path>"
        count = f" (count {finding.count})" if finding.count is not None else ""
        rendered.append(f"{finding.category} at {location!r}{count}: {finding.message}")
    return (
        f"Unsafe content survived the commit-review edit step: {len(findings)} finding(s) "
        f"remain in the sealed proposal ({'; '.join(rendered)}). The proposal is rejected on "
        "the engine's own evidence: untrack or ignore the affected paths and resubmit."
    )


def _evaluate_verdict(
    artifact: CommitReviewParsedArtifact,
    seal: Seal,
    diff_entries: list[DiffEntry],
    phase_name: str,
    ctx: PhaseContext,
) -> list[Event | CommitReviewEvent]:
    verdict_failure = _verdict_failure(artifact)
    if verdict_failure is not None:
        return [verdict_failure]

    if artifact.tree_sha != seal.tree_sha:
        return [
            CommitReviewGateFailed(
                detail=(
                    f"Verdict tree SHA {artifact.tree_sha!r} does not match the sealed "
                    f"proposal tree SHA {seal.tree_sha!r}; refusing to decide an "
                    "unbound verdict"
                )
            )
        ]

    verdict = artifact.verdict
    if verdict == "reject":
        if not verify_reject_citations(artifact.reason, diff_entries):
            logger.warning(
                "{}: reject citations could not be verified against pending diff: {!r}",
                phase_name,
                artifact.reason,
            )
            return [
                CommitReviewGateFailed(
                    detail=f"Reject citations could not be verified against pending diff: {artifact.reason!r}"
                )
            ]
        return [CommitReviewRejected(reason=artifact.reason or "", ref=seal.ref, hard=False)]

    surviving = _surviving_unsafe_findings(diff_entries, phase_name, ctx)
    if surviving:
        reason = _render_surviving_unsafe_reason(surviving)
        logger.warning("{}: {}", phase_name, reason)
        return [CommitReviewRejected(reason=reason, ref=seal.ref, hard=True)]

    clear_validation_retry_hint(ctx.workspace, phase_name, pipeline_policy=ctx.pipeline_policy)
    return [CommitReviewApproved(tree_sha=seal.tree_sha)]


def _resolve_workspace_root_safe(ctx: PhaseContext) -> tuple[Path | None, str]:
    try:
        repo_root_str = ctx.workspace.absolute_path(".")
        return Path(repo_root_str), ""
    except Exception as exc:
        return None, str(exc)


def _stage2_process_artifact(
    repo_root: Path,
    artifact: CommitReviewParsedArtifact,
    seal: Seal,
    diff_entries: list[DiffEntry],
    run_id: str,
    iteration: str,
    base: str,
    phase_name: str,
    ctx: PhaseContext,
) -> list[Event | CommitReviewEvent]:
    verdict_failure = _verdict_failure(artifact)
    if verdict_failure is not None:
        # The artifact's own grammar is the first word: a verdict that cannot
        # be read never reaches the tree binding and never runs an action.
        return [verdict_failure]

    binding_fault = _tree_binding_fault(artifact, seal)
    if binding_fault is not None:
        # The verdict names no tree the reviewer judged, so its edit step
        # must not run: the artifact is dropped and the reviewer is sent
        # back for a re-review of the sealed tree.
        _consume_commit_review_artifact(ctx)
        return _tree_binding_events(ctx, phase_name, binding_fault, artifact, seal)

    action_res = _handle_actions_and_reseal(
        repo_root, artifact, seal, diff_entries, run_id, iteration, base, phase_name, ctx
    )
    if isinstance(action_res, list):
        return action_res
    seal, diff_entries = action_res

    _consume_commit_review_artifact(ctx)
    # The edit step produced a new sealed tree, so the verdict is evaluated
    # against that tree: the binding follows the tree that will be committed.
    rebound = _rebind_verdict_to_sealed_tree(artifact, seal)
    return _evaluate_verdict(rebound, seal, diff_entries, phase_name, ctx)


def handle_commit_review_phase(
    effect: Effect,
    ctx: PhaseContext,
) -> list[Event | CommitReviewEvent]:
    """Handle the commit review phase.

    Orchestrates the two-stage review:

    1. Pre-emptively untracks engine-internal files and seeds canonical ignore/exclude.
    2. Seals pending working tree changes via proposal_seal.seal_pending.
    3. Stage 1: Evaluates deterministic tripwires on the sealed proposal tree. Hard tripwires reject immediately.
    4. Stage 2: Agent review. Provides proposal ref, diff, and soft tripwires, applies safe housekeeping actions, and validates verdict.
    """
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]
    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase_name = effect.phase
    repo_root, root_err = _resolve_workspace_root_safe(ctx)
    if repo_root is None:
        return maybe_succeed_after_identity_bound(
            ctx,
            phase_name,
            fingerprint="workspace-root",
            events=[
                PhaseFailureEvent(
                    phase=phase_name,
                    reason=f"Failed to resolve workspace root: {root_err}",
                    recoverable=True,
                    retry_in_session=True,
                    failure_category=FailureCategory.ARTIFACT_VALIDATION,
                )
            ],
        )

    try:
        ensure_git_initialized(str(repo_root))
    except Exception as exc:
        logger.warning("ensure_git_initialized failed: {}", exc)

    try:
        untrack_engine_internal_files(repo_root, is_agent_internal_path)
    except Exception as exc:
        logger.warning("untrack_engine_internal_files failed: {}", exc)

    try:
        from ralph.config.bootstrap import auto_seed_default_gitignore

        auto_seed_default_gitignore(repo_root)
    except Exception as exc:
        logger.warning("auto_seed_default_gitignore failed: {}", exc)

    try:
        from ralph.config.bootstrap import auto_seed_default_git_exclude

        auto_seed_default_git_exclude(repo_root)
    except Exception as exc:
        logger.warning("auto_seed_default_git_exclude failed: {}", exc)

    run_id, iteration = _resolve_run_id_and_iteration(ctx, effect)
    stage1_res = _run_stage1(repo_root, run_id, iteration, ctx, phase_name)
    if isinstance(stage1_res, list):
        return stage1_res
    seal, diff_entries, tripwire_report, base = stage1_res

    _persist_commit_review_context(ctx, seal, tripwire_report.soft, diff_entries)

    artifact_fingerprint = artifact_digest(ctx, COMMIT_REVIEW_ARTIFACT_PATH)
    artifact = _load_commit_review_artifact(ctx, phase_name)
    if artifact is None:
        return maybe_succeed_after_identity_bound(
            ctx,
            phase_name,
            fingerprint=f"missing-or-invalid:{artifact_fingerprint}",
            events=[
                PhaseFailureEvent(
                    phase=phase_name,
                    reason=f"Missing or invalid commit_review artifact at {COMMIT_REVIEW_ARTIFACT_PATH}",
                    recoverable=True,
                    retry_in_session=True,
                    failure_category=FailureCategory.ARTIFACT_VALIDATION,
                )
            ],
        )

    return _stage2_process_artifact(
        repo_root, artifact, seal, diff_entries, run_id, iteration, base, phase_name, ctx
    )


def _consume_commit_review_artifact(ctx: PhaseContext) -> None:
    """Drop a graded well-formed leftover so the next attempt needs a new submit."""
    try:
        if ctx.workspace.exists(COMMIT_REVIEW_ARTIFACT_PATH):
            ctx.workspace.delete(COMMIT_REVIEW_ARTIFACT_PATH)
    except Exception as exc:
        logger.warning("Failed to consume commit-review leftover artifact: {}", exc)


__all__ = [
    "COMMIT_REVIEW_ARTIFACT_PATH",
    "CommitReviewApplyReport",
    "CommitReviewApproved",
    "CommitReviewGateFailed",
    "CommitReviewLoopback",
    "CommitReviewRejected",
    "TripwireThresholds",
    "apply_commit_review_actions",
    "build_commit_review_retry_hint",
    "evaluate_tripwires",
    "handle_commit_review_phase",
    "is_agent_internal_path",
    "run_commit_review_stage1_preflight",
    "verify_reject_citations",
]
