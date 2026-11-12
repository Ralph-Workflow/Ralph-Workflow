from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from git import Repo
from loguru import logger

from ralph.config.agents_file_writer import write_agents_toml
from ralph.git.operations import create_commit, stage_files
from ralph.git.scoped_auto_commit import (
    ScopedCommitStatus,
    classify_target_for_commit,
    commit_deterministic_writes,
)

__all__ = ["BootstrapResult", "write_agents_with_backup"]


@dataclass(frozen=True)
class BootstrapResult:
    path: Path
    action: Literal["created", "skipped", "regenerated"]
    backup: Path | None = None


@dataclass(frozen=True)
class _ConfigCommitPlan:
    routing: str
    repo_root: Path | None
    pre_contents: dict[str, str | None]


def _repo_rel_path(target: Path, working_dir: Path) -> str:
    return target.relative_to(working_dir).as_posix()


def _prepare_config_commit(target: Path) -> _ConfigCommitPlan:
    routing = classify_target_for_commit(target, target)
    pre_contents: dict[str, str | None] = {}
    repo_root: Path | None = None
    if routing != "not_repo":
        from ralph.git.scoped_auto_commit import capture_pre_write_contents

        repo_anchor = target if target.exists() else target.parent
        while not repo_anchor.exists():
            repo_anchor = repo_anchor.parent
        repo_root = Path(Repo(repo_anchor, search_parent_directories=True).working_dir)
        pre_contents = capture_pre_write_contents(repo_root, [_repo_rel_path(target, repo_root)])
    return _ConfigCommitPlan(routing, repo_root, pre_contents)


def _commit_config_write(target: Path, plan: _ConfigCommitPlan) -> None:
    commit_paths: list[str] = []
    subject = "chore(config): noop"
    commit_repo_root = target.parent
    if plan.routing in ("tracked", "untracked_new") and plan.repo_root is not None:
        commit_repo_root = plan.repo_root
        commit_paths = [_repo_rel_path(target, plan.repo_root)]
        subject = f"chore(config): update {target.name}"
    else:
        logger.debug(
            "config bootstrap: {} classified {}; bypassing deterministic commit",
            target,
            plan.routing,
        )
    try:
        result = commit_deterministic_writes(
            commit_repo_root,
            paths=commit_paths,
            pre_contents=plan.pre_contents,
            subject=subject,
            create_commit_fn=create_commit,
            stage_fn=stage_files,
        )
        if result.status is ScopedCommitStatus.CREATED and result.sha:
            logger.debug("config bootstrap commit created: {}", result.sha[:8])
        elif result.status is ScopedCommitStatus.SKIPPED:
            logger.warning(
                "config bootstrap: {} was already dirty at HEAD; left for the user flow",
                target,
            )
        elif result.status is ScopedCommitStatus.FAILED:
            logger.warning("config bootstrap commit failed (non-fatal): {}", result.error)
    except Exception as exc:
        logger.warning("config bootstrap commit failed (non-fatal): {}", exc)


def write_agents_with_backup(
    source: Path, target: Path, force: bool, *, trigger: str | None = None
) -> BootstrapResult:
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_existed = target.exists()

    if pre_existed and not force:
        return BootstrapResult(target, "skipped", None)

    text = source.read_text(encoding="utf-8")
    actual_trigger = trigger or ("ralph --regenerate-config" if force else "ralph-workflow")
    plan = _prepare_config_commit(target)
    written = write_agents_toml(target, text, trigger=actual_trigger)

    if not written:
        return BootstrapResult(target, "skipped", None)

    backup = target.with_name(f"{target.name}.bak") if pre_existed else None
    action: Literal["created", "skipped", "regenerated"] = (
        "regenerated" if pre_existed else "created"
    )
    _commit_config_write(target, plan)
    return BootstrapResult(target, action, backup)
