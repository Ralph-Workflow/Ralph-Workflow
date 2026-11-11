"""Deterministic write helpers used by the bootstrap module.

Split from :mod:`ralph.config.bootstrap` so the bootstrap module stays under
the repository's 1000-line file cap enforced by
:mod:`ralph.testing.audit_repo_structure`. The agents-config writer is the
piece that grew past that cap; it lives here beside the repo-relative path
helper it depends on.

Every collaborator the writer uses is resolved from the ``ralph.config.bootstrap``
MODULE OBJECT at call time, so an operator- or test-supplied replacement
installed on that module (``ralph.config.write_agents_toml`` and
friends) still governs the write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from loguru import logger

from ralph.config._bootstrap_result import BootstrapResult

__all__ = ["_repo_rel_path", "_write_agents_with_backup"]


def _repo_rel_path(target: Path, working_dir: Path) -> str:
    """Return the repo-relative POSIX path of ``target`` inside ``working_dir``.

    wt-12: uses the LEXICAL path (``target`` as-is), NOT
    ``target.resolve(...)``. A tracked symlink (e.g. ``.agent/ralph-workflow.toml
    -> ../real.toml``) is tracked at its lexical path; ``resolve`` would
    swap the path for the symlink target and the deterministic commit
    helper would stage/commit the wrong blob (or warn
    "already dirty at HEAD" when the target file differs from HEAD),
    leaving the symlink's lexical entry dirty for the agent flow
    (DA-001 / DA-007 / DA-008 / DA-010).

    The caller is responsible for passing a target whose lexical path is
    inside ``working_dir``; bootstrap only writes paths inside the repo.
    """
    return target.relative_to(working_dir).as_posix()


def _write_agents_with_backup(
    source: Path, target: Path, force: bool, *, trigger: str | None = None
) -> BootstrapResult:
    """Safely write agents configuration, validating TOML and backing up prior edits.

    Routes writing through :func:`ralph.config.agents_file_writer.write_agents_toml`
    which validates TOML syntax via tomllib, backs up any existing configuration
    to ``<name>.bak``, uses a unique temporary file, and logs the trigger.

    Args:
        source: Bundled template to copy.
        target: Destination path.
        force: When True, overwrite target (with backup). When False, skip if target exists.
        trigger: Optional write trigger description. Defaults to "ralph --regenerate-config"
            when force is True, else "ralph-workflow".

    Returns:
        BootstrapResult describing what happened.
    """
    from ralph.config.bootstrap import (
        Repo,
        ScopedCommitStatus,
        classify_target_for_commit,
        commit_deterministic_writes,
        create_commit,
        stage_files,
        write_agents_toml,
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    pre_existed = target.exists()

    if pre_existed and not force:
        return BootstrapResult(target, "skipped", None)

    backup: Path | None = None
    commit_routing = classify_target_for_commit(target, target)
    pre_contents: dict[str, str | None] = {}
    repo_root: Path | None = None
    if commit_routing != "not_repo":
        from ralph.git.scoped_auto_commit import capture_pre_write_contents

        repo_anchor = target if target.exists() else target.parent
        while not repo_anchor.exists():
            repo_anchor = repo_anchor.parent
        repo_root = Path(Repo(repo_anchor, search_parent_directories=True).working_dir)
        pre_contents = capture_pre_write_contents(repo_root, [_repo_rel_path(target, repo_root)])

    text = source.read_text(encoding="utf-8")
    actual_trigger = trigger or ("ralph --regenerate-config" if force else "ralph-workflow")
    written = write_agents_toml(target, text, trigger=actual_trigger)

    bak_path = target.with_name(f"{target.name}.bak")
    if pre_existed and written:
        backup = bak_path

    action: Literal["created", "skipped", "regenerated"] = (
        "regenerated" if pre_existed else "created"
    )

    commit_paths: list[str] = []
    subject = "chore(config): noop"
    commit_repo_root = target.parent
    if commit_routing in ("tracked", "untracked_new"):
        if repo_root is None:
            raise RuntimeError("commit routing requires a resolved repository root")
        commit_repo_root = repo_root
        commit_paths = [_repo_rel_path(target, repo_root)]
        subject = f"chore(config): update {target.name}"
    else:
        logger.debug(
            "config bootstrap: {} classified {}; bypassing deterministic commit",
            target,
            commit_routing,
        )
    try:
        result = commit_deterministic_writes(
            commit_repo_root,
            paths=commit_paths,
            pre_contents=pre_contents,
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
    except Exception as exc:  # pragma: no cover - defensive, non-fatal
        logger.warning("config bootstrap commit failed (non-fatal): {}", exc)
    return BootstrapResult(target, action, backup)
