"""Bootstrap write path for Ralph's configuration files.

Every config file Ralph writes follows one contract: classify the target,
capture its pre-write state, write the file, and commit the result through
the deterministic writer. This module owns that contract together with the
agents-configuration write, whose TOML validation gives it a write path of
its own.

The agents configuration (``~/.config/ralph-workflow-agents.toml``) holds
the ``[agents.*]`` transport plumbing. It is written through
:func:`ralph.config.agents_file_writer.write_agents_toml`, which validates
the TOML, backs up an existing file, replaces the target atomically, and
reports whether the content actually changed. The shared commit routing
lives here so the shipped-template write and the agents write commit
through the same isolation primitive.
"""

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
    """Result of a bootstrap operation.

    Attributes:
        path: Target file path that was acted on.
        action: What happened: created, skipped, or regenerated.
        backup: Path to the .bak file if the original was backed up, else None.
    """

    path: Path
    action: Literal["created", "skipped", "regenerated"]
    backup: Path | None = None


@dataclass(frozen=True)
class _ConfigCommitPlan:
    """Pre-write commit routing state for one config target.

    Attributes:
        routing: The ``classify_target_for_commit`` outcome for the target.
        repo_root: The repository root that contains the target, or None when
            the target lives outside a git working tree.
        pre_contents: Pre-write content of every in-scope path, keyed by
            repository-relative POSIX path.
    """

    routing: str
    repo_root: Path | None
    pre_contents: dict[str, str | None]


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


def _prepare_config_commit(target: Path) -> _ConfigCommitPlan:
    """Classify the target and capture its pre-write state for commit routing.

    The target is classified BEFORE any write so the commit routing decision
    uses the pre-write state. The containing repository is the one
    ``classify_target_for_commit`` discovered for that target.

    Args:
        target: The config file that is about to be written.

    Returns:
        The routing state the deterministic commit consumes.
    """
    routing = classify_target_for_commit(target, target)
    pre_contents: dict[str, str | None] = {}
    repo_root: Path | None = None
    if routing != "not_repo":
        from ralph.git.scoped_auto_commit import capture_pre_write_contents

        # Use the closest existing ancestor for the Repo() probe: a
        # first-creation target does not exist on disk yet, and
        # ``Repo(target, search_parent_directories=True)`` raises
        # ``NoSuchPathError`` even with the parent-search fallback. The
        # containing repo is the same one ``classify_target_for_commit``
        # just discovered.
        repo_anchor = target if target.exists() else target.parent
        while not repo_anchor.exists():
            repo_anchor = repo_anchor.parent
        repo_root = Path(Repo(repo_anchor, search_parent_directories=True).working_dir)
        pre_contents = capture_pre_write_contents(repo_root, [_repo_rel_path(target, repo_root)])
    return _ConfigCommitPlan(routing, repo_root, pre_contents)


def _commit_config_write(target: Path, plan: _ConfigCommitPlan) -> None:
    """Commit a written config target through the deterministic writer.

    ``tracked`` and ``untracked_new`` targets are wholly Ralph-authored at
    this point, so they are committed under the fixed subject.
    ``untracked_ignored`` and ``not_repo`` targets bypass commit handling
    SILENTLY (debug log only, never a warning): an ignored path is not
    Ralph's place to commit into, and a non-repo tree has nothing to commit
    into. This is expressed as an empty ``paths`` list, for which
    ``commit_deterministic_writes`` is a guaranteed silent NOOP -- the call
    itself stays on the unconditional statement path after the write.

    Args:
        target: The config file that was written.
        plan: The routing state captured before the write.
    """
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
    # Empty ``paths`` short-circuits to a NOOP before any git access, so
    # the placeholder root is never touched for bypass classes.
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
    except Exception as exc:  # pragma: no cover - defensive, non-fatal
        logger.warning("config bootstrap commit failed (non-fatal): {}", exc)


def write_agents_with_backup(
    source: Path, target: Path, force: bool, *, trigger: str | None = None
) -> BootstrapResult:
    """Write the agents configuration, validating TOML and backing up prior edits.

    Routes the write through
    :func:`ralph.config.agents_file_writer.write_agents_toml`, which
    validates the TOML syntax via tomllib, backs up any existing
    configuration to ``<name>.bak``, writes through a unique temporary file,
    and reports whether the content changed.

    Args:
        source: Bundled template to copy.
        target: Destination path.
        force: When True, overwrite target (with backup). When False, skip if
            target exists.
        trigger: Optional write trigger description. Defaults to
            "ralph --regenerate-config" when force is True, else
            "ralph-workflow".

    Returns:
        BootstrapResult describing what happened. The action is "created" for
        a first creation, "regenerated" for a rewritten file, and "skipped"
        when nothing was written.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_existed = target.exists()

    if pre_existed and not force:
        return BootstrapResult(target, "skipped", None)

    text = source.read_text(encoding="utf-8")
    actual_trigger = trigger or ("ralph --regenerate-config" if force else "ralph-workflow")
    plan = _prepare_config_commit(target)
    written = write_agents_toml(target, text, trigger=actual_trigger)

    if not written:
        # The target already carries the shipped content: the writer left it
        # in place, so there is no backup to report and nothing to commit.
        return BootstrapResult(target, "skipped", None)

    backup = target.with_name(f"{target.name}.bak") if pre_existed else None
    action: Literal["created", "skipped", "regenerated"] = (
        "regenerated" if pre_existed else "created"
    )
    _commit_config_write(target, plan)
    return BootstrapResult(target, action, backup)
