from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.skills._capability_entry import CapabilityEntry
    from ralph.skills.manager import SkillManager


class _InstallSkills(Protocol):
    def __call__(self, workspace_root: Path) -> tuple[CapabilityEntry, list[str]]: ...


class _RetentionSweep(Protocol):
    def __call__(
        self,
        root: Path,
        *,
        keep_run_id: str | None,
        retention_max_age_seconds: float | None,
    ) -> None: ...


@dataclass(frozen=True)
class SetupDependencies:
    emit_warning: Callable[[str], None]
    print_user_global_update_hint: Callable[[], None]
    print_project_skill_conflict_hint: Callable[[list[str]], None]
    run_retention_sweep: _RetentionSweep
    skill_manager_factory: Callable[[], SkillManager]
    project_skills_need_install: Callable[[Path], bool]
    install_project_skills: _InstallSkills


def sync_shipped_skills(
    workspace_root: Path | None,
    *,
    keep_run_id: str | None,
    retention_max_age_seconds: float | None,
    dependencies: SetupDependencies,
) -> None:
    target_root = workspace_root or Path.cwd()
    try:
        update_available = dependencies.skill_manager_factory().check_skills_for_updates()
    except Exception as exc:
        dependencies.emit_warning(
            f"User-global skill update check failed (non-fatal): {exc}. Run `ralph --force-init-skills` to repair, or `ralph --diagnose` for details."
        )
        update_available = False
    if update_available:
        dependencies.print_user_global_update_hint()
    _run_user_global_skill_stages(dependencies.emit_warning)
    _auto_seed_project_ignores(target_root, dependencies.emit_warning)
    try:
        from ralph.git.operations import (
            create_commit as create_commit_impl,
        )
        from ralph.git.scoped_auto_commit import (
            ScopedCommitStatus,
            capture_pre_write_contents,
            snapshot_dirty_paths_strict,
        )
        from ralph.skills._auto_commit import commit_skill_writes
        from ralph.skills._installer import _skill_sync_written_paths
        from ralph.skills._installer_candidates import (
            _candidate_skill_paths,
        )

        create_commit = create_commit_impl
        try:
            if dependencies.project_skills_need_install(target_root):
                pre_tree = snapshot_dirty_paths_strict(target_root)
                if pre_tree is None:
                    dependencies.emit_warning(
                        "Project-scope skill install: working tree could not be read; "
                        "running the install but skipping the deterministic chore "
                        "commit (will retry on next run)."
                    )
                candidates = _candidate_skill_paths(target_root)
                if pre_tree is not None:
                    pre_contents = capture_pre_write_contents(target_root, candidates)
                else:
                    pre_contents = dict.fromkeys(candidates, None)
                _, failures = dependencies.install_project_skills(target_root)
                if failures:
                    dependencies.print_project_skill_conflict_hint(failures)
                if pre_tree is not None:
                    written_paths = _skill_sync_written_paths(target_root, candidates, pre_contents)
                    if written_paths:
                        written_pre_contents = {p: pre_contents.get(p) for p in written_paths}
                        result = commit_skill_writes(
                            target_root,
                            written_paths=written_paths,
                            pre_contents=written_pre_contents,
                            create_commit_fn=create_commit,
                        )
                        if result.status is ScopedCommitStatus.CREATED and result.sha:
                            logger.info("Auto-committed skill updates: {}", result.sha[:8])
                        elif result.status is ScopedCommitStatus.FAILED:
                            dependencies.emit_warning(
                                f"Skill auto-commit failed (non-fatal): {result.error}. "
                                "The run continues with the new skill content uncommitted; "
                                "commit manually or re-run to retry."
                            )
                        elif result.status is ScopedCommitStatus.SKIPPED and result.skipped_paths:
                            logger.warning(
                                "Skill auto-commit skipped {} path(s) already dirty at HEAD; "
                                "left for the agent flow",
                                len(result.skipped_paths),
                            )
        except Exception as exc:
            logger.debug("Project-scope skill install failed (non-fatal): {}", exc)
            dependencies.emit_warning(
                f"Project-scope skill install failed (non-fatal): {exc}. "
                "The run continues with the new skill content uncommitted; "
                "commit manually or re-run to retry."
            )
    except Exception as exc:
        logger.debug("Skill auto-commit failed (non-fatal): {}", exc)
        dependencies.emit_warning(
            f"Skill auto-commit failed (non-fatal): {exc}. The run continues with the new skill content uncommitted; commit manually or re-run to retry."
        )
    dependencies.run_retention_sweep(
        target_root, keep_run_id=keep_run_id, retention_max_age_seconds=retention_max_age_seconds
    )


def _auto_seed_project_ignores(target_root: Path, emit_warning: Callable[[str], None]) -> None:
    try:
        from ralph.config.bootstrap import (
            auto_seed_default_git_exclude,
            auto_seed_default_gitignore,
        )

        auto_seed_default_gitignore(target_root)
        auto_seed_default_git_exclude(target_root)
    except Exception as exc:
        emit_warning(
            f"Project .gitignore/.git/info/exclude auto-seed failed (non-fatal): {exc}. Re-run `ralph` or check file permissions on .gitignore and .git/info/exclude."
        )


def _run_user_global_skill_stages(emit_warning: Callable[[str], None]) -> None:
    try:
        _run_user_global_skill_migration(emit_warning)
    except Exception as exc:
        logger.debug("User-global skill migration failed (non-fatal): {}", exc)
    try:
        _run_universal_skill_update(emit_warning)
    except Exception as exc:
        logger.debug("Universal skill update failed (non-fatal): {}", exc)


_UNIVERSAL_SKILL_UPDATE_THREAD_NAME = "ralph-universal-skill-update"


def _run_user_global_skill_migration(emit_warning: Callable[[str], None]) -> None:
    try:
        from ralph.skills._prune import _prune_user_global_skill_roots

        failures = _prune_user_global_skill_roots()
    except Exception as exc:
        logger.debug("User-global skill migration failed (non-fatal): {}", exc)
        emit_warning(
            f"User-global skill migration failed (non-fatal): {exc}. "
            "The run continues; re-run `ralph --force-init-skills` to repair the user-global skill tree."
        )
        return
    for code in failures:
        emit_warning(f"User-global skill migration: {code}")


def _run_universal_skill_update(emit_warning: Callable[[str], None]) -> None:
    try:
        import os
        import threading

        from ralph.skills.gating import universal_skill_updates_enabled

        enabled = universal_skill_updates_enabled(os.environ)
    except Exception as exc:
        logger.debug("Universal skill update gate failed (non-fatal): {}", exc)
        return
    if not enabled:
        return
    try:
        thread = threading.Thread(
            target=_universal_skill_update_worker,
            args=(emit_warning,),
            name=_UNIVERSAL_SKILL_UPDATE_THREAD_NAME,
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        logger.debug("Universal skill update could not start (non-fatal): {}", exc)
        emit_warning(f"Universal skill update could not start (non-fatal): {exc}.")


def _universal_skill_update_worker(emit_warning: Callable[[str], None]) -> None:
    try:
        from datetime import UTC, datetime

        from ralph.skills import upstream

        outcome = upstream.maybe_update_universal_skills(
            enabled=True,
            now=datetime.now(UTC),
            http=upstream.default_http_client(),
        )
    except Exception as exc:
        logger.debug("Universal skill update failed (non-fatal): {}", exc)
        emit_warning(f"Universal skill update failed (non-fatal): {exc}.")
        return
    for message in outcome.messages:
        emit_warning(f"Universal skill update: {message}")
    for name in outcome.preserved:
        if any(name in message for message in outcome.messages):
            continue
        emit_warning(f"Universal skill update: {name} was preserved (local edits kept).")


def run_start_retention_sweep(
    target_root: Path,
    *,
    keep_run_id: str | None,
    retention_max_age_seconds: float | None,
    emit_warning: Callable[[str], None],
) -> None:
    try:
        from ralph.workspace.agent_dir_retention import (
            DEFAULT_MAX_AGE_SECONDS,
            process_retention_coordinator,
            sweep_agent_dir,
        )

        removed = sweep_agent_dir(
            target_root,
            keep_run_id=keep_run_id,
            max_age_seconds=retention_max_age_seconds
            if retention_max_age_seconds is not None
            else DEFAULT_MAX_AGE_SECONDS,
            coordinator=process_retention_coordinator(),
        )
        if removed:
            logger.debug("Retention sweep removed {} stale .agent entries", removed)
    except Exception as exc:
        emit_warning(
            f"Retention sweep failed (non-fatal): {exc}. The run continues without cleanup; check .agent/ permissions."
        )
    if keep_run_id is not None:
        try:
            from ralph.workspace.agent_dir_retention import register_active_run

            register_active_run(target_root, keep_run_id)
        except Exception as exc:
            logger.debug("Active-run registration failed (non-fatal): {}", exc)
