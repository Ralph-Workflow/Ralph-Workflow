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


def sync_shipped_skills(  # noqa: PLR0912
    # PLR0912 (too many branches): the run-start flow has explicit
    # gates for user-global, project-scope install, gitignore
    # auto-seed, skill auto-commit (with pre-write hash discipline,
    # pre-snapshot, diff capture, and CREATED/FAILED/SKIPPED
    # outcome reporting), and the retention sweep. Each gate is a
    # distinct stage with its own warning surface, so flattening
    # them into a single branch would lose the per-stage
    # non-fatal warning boundary the run contract depends on.
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
    # wt-012 DA-001/DA-008: capture the pre-write snapshot BEFORE the
    # first install runs, then route the install through the
    # ``with_diff`` wrapper so the byte-exact set of paths the install
    # actually wrote is captured for the deterministic chore commit.
    # The first install keeps the original ``_project_skills_need_install``
    # gate so the test suite's monkeypatch on the plain
    # ``install_project_baseline_skills`` still drives the call.
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
        from ralph.git.operations import (
            create_commit as create_commit_impl,
        )
        from ralph.git.scoped_auto_commit import (
            ScopedCommitStatus,
            capture_pre_write_contents,
            snapshot_dirty_paths_strict,
        )
        from ralph.skills._auto_commit import commit_skill_writes
        from ralph.skills._installer_candidates import (
            _candidate_skill_paths,
            _diff_written_paths,
        )

        create_commit = create_commit_impl
        # wt-012 DA-001/DA-008: capture the pre-write hash for every
        # candidate skill path BEFORE the install runs, then run the
        # install (the dependency-injected ``install_project_skills``
        # is the mockable surface the test suite patches), then
        # compute the diff and commit. The install runs whenever
        # ``_project_skills_need_install`` says so (preserving the
        # original gate the test suite depends on); the chore commit
        # is the only step that additionally requires a valid git
        # pre-snapshot.
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
                    written_paths = _diff_written_paths(target_root, candidates, pre_contents)
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
