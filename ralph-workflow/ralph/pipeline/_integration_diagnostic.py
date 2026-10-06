"""Supervised inspection when retained integration lacks a completion receipt."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.pipeline.run_loop import _LoopContext


def diagnostic_needed(root: Path, record: IntegrationRecord) -> bool:
    from ralph.git.merge import MERGE_STATE_NONE, MERGE_STATE_UNKNOWN, merge_state, unmerged_paths
    from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
    from ralph.pipeline._retained_resolution_scope import original_conflict_paths

    if record.diagnostic_evidence:
        return True
    operation_root = Path(record.owning_worktree) if record.owning_worktree else root
    try:
        if record.rebase_continue_pending:
            from ralph.pipeline._pending_rebase_continue import pending_rebase_identity_matches

            return not pending_rebase_identity_matches(operation_root, record)
        if record.merge_commit_pending:
            from ralph.pipeline._pending_merge_commit import pending_merge_identity_matches

            return not pending_merge_identity_matches(operation_root, record)
        unmerged = unmerged_paths(operation_root)
        unreadable = "<unmerged-path-query-failed>" in unmerged
        missing_scope = (record.resolving_merge or record.resolving_rebase) and not (
            record.resolving_paths or unmerged or original_conflict_paths(operation_root)
        )
        if unreadable or missing_scope:
            return True
        merge = merge_state(operation_root)
        return merge == MERGE_STATE_UNKNOWN or (
            merge == MERGE_STATE_NONE and not rebase_in_progress_at(operation_root)
        )
    except Exception:
        return True


def diagnose_retained_integration(ctx: _LoopContext, failure: str) -> bool:
    """Inspect missing evidence under both worktree leases; never accept agent success as proof."""
    from contextlib import ExitStack

    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_transaction import integration_transaction
    from ralph.pipeline.conflict_resolution.session import (
        invoke_resolution_agent,
        resolution_chain_agents,
    )
    from ralph.workspace.context import workspace_context
    from ralph.workspace.scope import WorkspaceScope

    pipeline_deps = ctx.pipeline_deps
    if pipeline_deps is None:
        logger.critical("Integration evidence inspection requires agent dependencies: {}", failure)
        return False
    with ExitStack() as leases:
        if not leases.enter_context(integration_transaction(ctx.workspace_scope.root)):
            return False
        record = read_record(ctx.workspace_scope.root)
        if record is None:
            return False
        operation_root = ctx.workspace_scope.root
        if record.operation_kind == "target_reconcile" and record.owning_worktree:
            operation_root = Path(record.owning_worktree)
            if operation_root != ctx.workspace_scope.root and not leases.enter_context(
                integration_transaction(operation_root)
            ):
                return False

        def invoke(agent: str, prompt: Path) -> bool:
            with workspace_context(operation_root):
                return invoke_resolution_agent(
                    agent_name=agent, prompt_path=prompt, config=ctx.config,
                    pipeline_deps=pipeline_deps, workspace_scope=WorkspaceScope(operation_root),
                    policy_bundle=ctx.policy_bundle, display=ctx.active_display,
                    display_context=ctx.display_context,
                    operator_cap_seconds=ctx.config.conflict_resolution.total_resolution_cap_seconds,
                    require_completion_evidence=True,
                )

        try:
            before = record.diagnostic_evidence or _diagnostic_snapshot(operation_root)
            if not record.diagnostic_evidence:
                record = record.model_copy(update={
                    "diagnostic_evidence": before,
                    "diagnostic_ownership": _ownership_fingerprint(record),
                })
            handoff_integration_diagnostic(
                root=ctx.workspace_scope.root, record=record, failure=failure,
                agents=tuple(agent for agent in resolution_chain_agents(ctx.policy_bundle)
                             if ctx.registry.get(agent) is not None), invoke=invoke,
            )
            if _diagnostic_snapshot(operation_root) != before:
                logger.critical("Integration diagnostic changed protected Git evidence; landing withheld")
            retained = read_record(ctx.workspace_scope.root)
            if retained is None or _ownership_fingerprint(retained) != record.diagnostic_ownership:
                write_record(ctx.workspace_scope.root, record)
                logger.critical("Integration diagnostic changed ownership; original record restored")
            return False
        except Exception as exc:
            logger.critical("Integration diagnostic retained ownership after failure: {}", exc)
            return False


def _ownership_fingerprint(record: IntegrationRecord) -> str:
    stable = record.model_copy(update={
        "diagnostic_evidence": (), "diagnostic_ownership": None,
        "merge_commit_repair_attempts": 0, "repair_last_error": None,
    })
    return hashlib.sha256(stable.model_dump_json().encode()).hexdigest()


def diagnostic_evidence_unchanged(root: Path, record: IntegrationRecord) -> bool:
    if not record.diagnostic_evidence:
        return True
    if record.diagnostic_ownership != _ownership_fingerprint(record):
        return False
    observed = dict(_diagnostic_snapshot(root))
    return set(observed) == {key for key, _value in record.diagnostic_evidence} and all(
        value == "unreadable" or observed.get(key) == value
        for key, value in record.diagnostic_evidence
    )


def release_verified_diagnostic(root: Path, record: IntegrationRecord) -> str | None:
    try:
        if not diagnostic_evidence_unchanged(root, record):
            return "diagnostic evidence changed; original evidence must be restored before recovery"
        write_record(root, record.model_copy(update={
            "diagnostic_evidence": (), "diagnostic_ownership": None,
        }))
        return None
    except Exception as exc:
        return f"diagnostic evidence unreadable; recovery retained: {exc}"


def _diagnostic_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    from ralph.git.subprocess_runner import run_git
    from ralph.pipeline._pending_repair_edits import capture_commit_controls, capture_unstaged_work

    observations: list[tuple[str, str]] = []
    for args in (
        ("rev-parse", "--verify", "HEAD"),
        ("for-each-ref", "--format=%(refname) %(objectname)"),
        ("ls-files", "--stage", "-z"),
    ):
        result = run_git(args, cwd=root, label="integration:diagnostic-evidence")
        value = "unreadable" if result.returncode else hashlib.sha256(result.stdout.encode()).hexdigest()
        observations.append((args[0], value))
    captures: tuple[tuple[str, Callable[[], str]], ...] = (
        ("work", lambda: repr(sorted(capture_unstaged_work(root).items()))),
        ("controls", lambda: capture_commit_controls(root)),
    )
    for name, capture in captures:
        try:
            value = hashlib.sha256(capture().encode()).hexdigest()
        except (RuntimeError, OSError):
            value = "unreadable"
        observations.append((name, value))
    metadata = run_git(("rev-parse", "--absolute-git-dir"), cwd=root, label="integration:diagnostic-gitdir")
    if metadata.returncode:
        observations.append(("metadata", "unreadable"))
    else:
        gitdir = Path(metadata.stdout.strip())
        files = set(DEFAULT_FILE_BACKEND.glob(gitdir, "rebase-merge/*"))
        files.update(DEFAULT_FILE_BACKEND.glob(gitdir, "rebase-apply/*"))
        files.update(gitdir / name for name in (
            "MERGE_HEAD", "MERGE_MSG", "REBASE_HEAD", "ORIG_HEAD", "AUTO_MERGE",
            "CHERRY_PICK_HEAD", "logs/HEAD",
        ))
        branch = run_git(("symbolic-ref", "--quiet", "HEAD"), cwd=root, label="integration:diagnostic-branch")
        if branch.returncode == 0:
            location = run_git(("rev-parse", "--git-path", f"logs/{branch.stdout.strip()}"),
                               cwd=root, label="integration:diagnostic-reflog")
            if location.returncode == 0:
                path = Path(location.stdout.strip())
                files.add(path if path.is_absolute() else root / path)
        for path in sorted(files):
            try:
                value = (hashlib.sha256(DEFAULT_FILE_BACKEND.read_bytes(path)).hexdigest()
                         if DEFAULT_FILE_BACKEND.exists(path) else "absent")
            except OSError:
                value = "unreadable"
            observations.append((str(path), value))
    return tuple(observations)


def handoff_integration_diagnostic(
    *, root: Path, record: IntegrationRecord, failure: str,
    agents: Sequence[str], invoke: Callable[[str, Path], bool],
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    """Rotate inspection candidates without authorizing publication or record cleanup."""
    if not agents:
        return False
    agent = agents[record.merge_commit_repair_attempts % len(agents)]
    prompt = root / ".agent" / "tmp" / "integration_diagnostic_prompt.md"
    content = (
        "Inspect the retained integration blocker and repair only its environment.\n"
        f"Diagnostic data: {failure[:8192]}\n"
        f"Recorded target: {record.target}\n"
        f"Original feature: {record.pre_feature_sha}\n"
        f"Original target: {record.pre_target_sha}\n"
        f"Operation worktree: {record.owning_worktree or root}\n"
        "Completion evidence is missing or unreadable. Examine Git history, reflogs, "
        "operation metadata, executable availability, and the command diagnostic. "
        "Restore access to existing evidence only when independently justified. "
        "Do not create, alter, or delete recovery records, receipts, refs, indexes, "
        "operation metadata, hooks, signing configuration, or source files. "
        "Do not reset, abort, stash, commit, rebase, merge, stage, or bypass checks. "
        "Preserve all unfinished work. Declare_complete with observed cause, "
        "evidence locations, and any environment repair. Agent success does not "
        "authorize landing; Ralph independently retries and verifies Git evidence.\n"
    )
    backend.mkdir(prompt.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, prompt, content)
    write_record(root, record.model_copy(update={
        "merge_commit_repair_attempts": record.merge_commit_repair_attempts + 1,
        "repair_last_error": failure[:8192],
    }), backend=backend)
    return invoke(agent, prompt)
