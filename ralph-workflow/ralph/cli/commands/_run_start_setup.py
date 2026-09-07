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
    try:
        if dependencies.project_skills_need_install(target_root):
            _, failures = dependencies.install_project_skills(target_root)
            if failures:
                dependencies.print_project_skill_conflict_hint(failures)
    except Exception as exc:
        dependencies.emit_warning(
            f"Project-scope skill install failed (non-fatal): {exc}. Run `ralph --force-init-skills` to retry, or check file permissions on .agent/skills/."
        )
    try:
        from ralph.config.bootstrap import (
            auto_seed_default_git_exclude,
            auto_seed_default_gitignore,
        )

        auto_seed_default_gitignore(target_root)
        auto_seed_default_git_exclude(target_root)
    except Exception as exc:
        dependencies.emit_warning(
            f"Project .gitignore/.git/info/exclude auto-seed failed (non-fatal): {exc}. Re-run `ralph` or check file permissions on .gitignore and .git/info/exclude."
        )
    try:
        from ralph.git.operations import create_commit as create_commit_impl
        from ralph.skills._auto_commit import commit_skill_updates

        create_commit = create_commit_impl
        sha = commit_skill_updates(target_root, create_commit)
        if sha:
            logger.info("Auto-committed skill updates: {}", sha[:8])
    except Exception as exc:
        logger.debug("Skill auto-commit failed (non-fatal): {}", exc)
        dependencies.emit_warning(
            f"Skill auto-commit failed (non-fatal): {exc}. The run continues with the new skill content uncommitted; commit manually or re-run to retry."
        )
    dependencies.run_retention_sweep(
        target_root, keep_run_id=keep_run_id, retention_max_age_seconds=retention_max_age_seconds
    )


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
