"""Generic deterministic auto-commit over an explicit path scope.

Shared machinery behind Ralph's engine-owned chore commits — the skill-tree
sync (``ralph.skills._auto_commit``) and the project-policy readiness sync
(``ralph.project_policy._auto_commit``). Each caller supplies its path
scopes, deterministic subject, and body builder; this module owns the git
mechanics that make the commit safe:

* dirty-path discovery per scope via ``git status --porcelain
  --untracked-files=all -- <scope>`` with a defensive re-filter;
* preservation of the user's exact staged state for paths OUTSIDE the
  scope (snapshot via ``git ls-files --stage``, restore via ``git
  update-index --cacheinfo``) so a partially staged file is never
  silently committed or corrupted;
* best-effort semantics: a non-git workspace, a clean scope, or any
  ``OSError`` / ``GitCommandError`` returns an explicit ``ScopedCommitResult``
  with status ``NOT_REPO`` / ``NOOP`` / ``FAILED`` so callers can surface
  the outcome to the user — a broken git state must never block the
  pipeline, but it MUST be reported, never silently swallowed.

A scope string ending in ``/`` matches every path under that directory;
any other scope string matches that exact repo-relative file.

The single isolation primitive every deterministic background writer must
route through is :func:`commit_deterministic_writes` (added in wt-012). A
callable that knows the byte-exact set of paths it just wrote, and each
path's pre-write content hash, commits ONLY the paths whose HEAD blob
equals the recorded pre-write hash. A path that was already dirty before
the deterministic writer touched it (HEAD != pre-write hash, e.g. an
agent-edited AGENTS.md that a bootstrap then rewrote) is SKIPPED with a
warning and left uncommitted for the agent flow; it can never enter a
fixed-message commit. A path whose pre-write hash equals HEAD and whose
post-write content still equals HEAD is a no-op (no commit). This is the
single isolation guarantee that keeps a deterministic chore commit from
sweeping in agent or user changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo
from loguru import logger

from ralph.git._dirty_paths import (
    _list_dirty_paths,
    list_dirty_paths,
    path_in_scope,
    snapshot_dirty_paths_strict,
)
from ralph.git._index_snapshots import (
    _GIT_LS_FILES_META_FIELDS,
    _GIT_LS_FILES_PATH_PARTS,
    _capture_staged_paths,
    _restore_pre_staged_index,
    _snapshot_pre_staged_index,
)
from ralph.git._transition_guard import drop_transition_conflicts
from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol

    class CreateCommitFn(Protocol):
        def __call__(
            self, repo_root: Path | str, message: str, *, expected_head: str
        ) -> CommitCreationResult: ...


class ScopedCommitStatus(Enum):
    """Outcome category returned by :func:`commit_scoped_updates` and friends.

    Every status is reportable. The legacy helper returned ``None`` for
    both "clean scope" (NOOP) and "git error" (FAILED), so callers could
    not distinguish them; wt-012 makes them explicit so failures are
    visible per the product criterion that a commit failure is reported
    clearly and never silently swallowed.
    """

    CREATED = "created"
    NOOP = "noop"  # No dirty in-scope paths; nothing to commit.
    NOT_REPO = "not_repo"  # ``repo_root`` is not inside a git working tree.
    FAILED = "failed"  # Any ``OSError`` / ``GitCommandError`` from the attempt.
    SKIPPED = "skipped"  # Every in-scope path was already dirty at HEAD, or its staging would sweep a dirty skipped descendant (wt-012 DA-003/DA-008).


@dataclass(frozen=True)
class ScopedCommitResult:
    """Typed outcome for a scoped deterministic commit attempt.

    Attributes:
        status: Outcome category (CREATED / NOOP / NOT_REPO / FAILED / SKIPPED).
        sha: The commit SHA on ``CREATED``, else ``None``.
        skipped_paths: Paths left uncommitted: those already dirty at
            HEAD (HEAD != pre-write hash) and any ancestor-transition
            entries whose staging would have swept such a path's
            deletion into the commit (wt-012 DA-003/DA-008).
            Empty on every non-SKIPPED status.
        error: Human-readable failure detail for ``FAILED``; ``None``
            otherwise.
    """

    status: ScopedCommitStatus
    sha: str | None = None
    skipped_paths: tuple[str, ...] = ()
    error: str | None = None


# Sentinel returned by :func:`_read_head_blob_sha` when the HEAD metadata
# probe itself failed (``GitCommandError``). MUST be distinguishable from
# ``None`` ("absent at HEAD") so a broken index can never masquerade as a
# brand-new path and authorize staging (wt-012 DA-006/DA-011: fail closed).
_HEAD_PROBE_FAILED: str = "__HEAD_PROBE_FAILED__"


def _symlink_ancestor_path(repo: Repo, rel_path: str) -> str | None:
    """Return the repo-relative path of the first symlink ancestor of ``rel_path``.

    Used by :func:`commit_deterministic_writes` to rewrite a
    lexically-unreachable path (its parent is a symlink) to the
    nearest symlink ancestor so ``git add --all`` can stage both the
    new symlink AND the tracked-descendant deletions atomically. wt-012
    PA-002. Walks the parent directories from the closest to the
    repo root; returns the first symlink encountered as a
    repo-relative POSIX path, or ``None`` if no ancestor is a symlink.

    The repo-relative form is the LEXICAL path of the symlink itself
    (wt-012 DA-003/DA-010: resolving it returned the canonical target
    directory, sweeping unrelated user-dirty files into the chore
    commit); the lexical path scopes staging to the symlink entry.
    """
    try:
        working_dir = repo.working_dir
    except (OSError, ValueError):
        return None
    if not working_dir:
        return None
    abs_path = Path(working_dir) / rel_path
    for parent in abs_path.parents:
        try:
            if parent.is_symlink():
                return parent.relative_to(working_dir).as_posix()
        except (OSError, ValueError):
            return None
    return None


def _has_symlink_ancestor(abs_path: Path) -> bool:
    """Return True when any parent directory of ``abs_path`` is a symlink.

    Used by :func:`_git_blob_sha` to detect the dir→symlink transition
    shape (wt-012 PA-002): ``git hash-object <rel_path>`` follows
    ancestor symlinks on the filesystem, so a deleted sibling dir
    replaced by a symlink to a target that contains a same-named file
    would hash bytes that no longer belong to the lexical path. The
    consumer (the post-write HEAD-vs-on-disk equality check) treats
    ``_ANCESTOR_SYMLINK_DIRTY`` as a real change so the deletion is
    always staged.

    Walks the path's parents from the closest to the root; stops at
    the first symlink. ``abs_path`` itself is NOT inspected (a leaf
    symlink is the expected leaf-blob case handled above).
    """
    try:
        parents = list(abs_path.parents)
    except OSError:
        return False
    for parent in parents:
        # ``parent == abs_path`` can never be a symlink for a relative
        # path; the loop terminates on the root ancestor. ``is_symlink``
        # returns False for nonexistent parents, so a deleted file under
        # a non-symlink ancestor is correctly classified as "no
        # symlink ancestor" (the file is just missing, which
        # ``git hash-object`` will surface as an error / None result).
        try:
            if parent.is_symlink():
                return True
        except OSError:
            return True
    return False


# Sentinel returned by :func:`_git_blob_sha` when the path's on-disk
# content CANNOT be safely compared against HEAD because an ancestor
# directory is a symlink. The consumer in :func:`commit_deterministic_writes`
# treats this as a real change (forces staging) so a dir→symlink
# transition cannot be silently misclassified as a no-op. MUST be
# distinguishable from ``None`` ("path is missing or unreadable") so the
# no-op short-circuit ``on_disk_sha is not None and on_disk_sha == head_sha``
# never accidentally matches.
_ANCESTOR_SYMLINK_DIRTY: str = "__ANCESTOR_SYMLINK_DIRTY__"


def _read_head_blob_sha(repo: Repo, path: str) -> str | None:
    """Return the HEAD blob SHA for ``path`` or ``None`` if not tracked / absent.

    Used by :func:`commit_deterministic_writes` to compare the caller's
    recorded pre-write content hash against the actual HEAD state. A
    missing file at HEAD (``None``) is a valid "no prior content" case;
    a missing path that the deterministic writer is adding fresh must
    show up as ``None`` in the caller's pre-write map, and this helper
    must return ``None`` too, so the SHA-equality check is correct.

    A probe FAILURE (``GitCommandError`` from the index metadata read)
    returns the ``_HEAD_PROBE_FAILED`` sentinel instead of ``None`` --
    the caller treats it as fail-closed (``FAILED``), never as
    "absent at HEAD" (wt-012 DA-006/DA-011).
    """
    try:
        raw: object = repo.git.ls_files("--stage", "--", path)
    except GitCommandError:
        return _HEAD_PROBE_FAILED
    if not isinstance(raw, str):
        # Non-string output means the probe itself is broken; fail closed
        # rather than treating the path as absent at HEAD (wt-012 DA-006).
        return _HEAD_PROBE_FAILED
    if not raw.strip():
        return None
    # CRITICAL (wt-012 PA-002): parse the path field and only return the SHA
    # when the returned pathname equals the requested path EXACTLY. The
    # ``git ls-files --stage -- <path>`` output is path-relative, but a
    # path argument of ``a`` will return every tracked entry whose name
    # STARTS WITH ``a/`` (e.g. ``a/b``, ``a/c.txt``). The previous
    # implementation returned the FIRST line's blob, which silently
    # attributed a descendant's blob to the ancestor path -- so a
    # directory root replaced by a symlink captured ``pre_sha = None``
    # (directory is un-hashable) while ``head_sha`` was the blob of a
    # tracked descendant, putting the caller in the
    # ``pre_sha is None and head_sha is not None`` SKIP branch and
    # preventing the new symlink root from ever being committed.
    for line in raw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != _GIT_LS_FILES_PATH_PARTS:
            continue
        meta = parts[0].split()
        if len(meta) < _GIT_LS_FILES_META_FIELDS:
            continue
        if parts[1] == path:
            return meta[1]
    return None


def _git_blob_sha(repo: Repo, rel_path: str) -> str | None:  # noqa: PLR0911
    # PLR0911 (too many returns): each return maps a distinct
    # failure mode to None (working_dir unreadable, working_dir
    # empty, symlink target unreadable, symlink target unencodable,
    # hash-object failure) plus the symlink fast-path's success
    # return. Folding them into a sentinel would lose the
    # fail-closed boundary each guard enforces.
    """Return the git blob SHA-1 of the on-disk content at ``rel_path``.

    Uses ``git hash-object <path>`` so the value is git's blob hash,
    matching what :func:`_read_head_blob_sha` returns for HEAD. This
    makes the caller's recorded pre-write hash directly comparable
    to HEAD (a deterministic writer passes pre-write = ``git
    hash-object`` of the file as it stood BEFORE the write). Symlinks
    are handled natively: git stores them as blobs containing the link
    target, so the on-disk and HEAD blob hashes are the same shape.

    Returns ``None`` for unreadable / missing / non-git paths.

    Note: ``git hash-object`` cannot read a directory symlink (it tries
    to open the path as a regular file and fails). For that case we
    compute the blob hash directly from the symlink target bytes
    using git's ``blob <len>\\0<target>`` envelope, which is the same
    blob layout git itself uses for symlinks. The result is byte-for-byte
    comparable to what ``git hash-object --stdin`` produces for the
    same target (wt-012 DA-001/DA-008: the producer-level install
    creates directory symlinks for the project sibling roots).
    """
    try:
        working_dir = repo.working_dir
    except (OSError, ValueError):
        return None
    if not working_dir:
        return None
    abs_path = Path(working_dir) / rel_path
    # Ancestor-symlink safety (wt-012 PA-002 / DA-010): if any parent
    # directory of ``rel_path`` is a symlink, the lexical path no longer
    # exists as its own directory entry, so ANY content reachable
    # through it -- a regular file OR a symlink leaf -- belongs to the
    # link target's tree, not to this path. The guard MUST run before
    # the leaf fast path below: otherwise a symlink leaf under a
    # freshly-installed ancestor symlink (e.g. a replaced sibling dir
    # whose canonical ships a same-named symlink) would hash its link
    # target bytes and -- when those bytes accidentally equal the
    # recorded pre-write hash -- be misclassified as "unchanged", which
    # drops the path from the producer's written set and lets the
    # ancestor commit silently sweep its deletion (wt-012 DA-003/DA-012).
    # Refuse the comparison instead: return a non-None sentinel the
    # consumer treats as "real change" so the path is always staged
    # through its nearest symlink ancestor.
    if _has_symlink_ancestor(abs_path):
        return _ANCESTOR_SYMLINK_DIRTY
    # Directory-symlink fast path: ``git hash-object`` cannot open a
    # directory symlink because it would have to read the directory
    # contents. Git itself stores symlink blobs as
    # ``blob <len>\\0<target>``, so we compute the SHA-1 directly from
    # the target bytes. The hash matches ``git hash-object --stdin``
    # exactly.
    if abs_path.is_symlink():
        try:
            link_target = abs_path.readlink()
        except OSError:
            return None
        try:
            target_bytes = str(link_target).encode("utf-8")
        except UnicodeEncodeError:
            return None
        blob = b"blob " + str(len(target_bytes)).encode("ascii") + b"\x00" + target_bytes
        return hashlib.sha1(blob).hexdigest()
    try:
        hashed: object = repo.git.hash_object(abs_path)
    except (GitCommandError, OSError):
        return None
    if not isinstance(hashed, str):
        return None
    return hashed.strip() or None


def _resolved_descendant_sha(repo: Repo, rel_path: str) -> str | None:
    """Return the on-disk blob SHA of ``rel_path`` resolved through any ancestor symlink.

    Used by :func:`commit_deterministic_writes` to detect a
    pre-write-dirty descendant under a freshly-installed symlink
    ancestor (wt-012 DA-003/DA-008/DA-010/DA-012). The producer
    captures the pre-write content hash BEFORE the install runs, so
    a user change that lands BETWEEN the capture and the install
    makes the recorded pre-write hash stale. The on-disk state at
    commit time resolves THROUGH the new symlink to whatever the
    install's canonical target now contains -- a state the producer
    did not author and cannot be sure belongs to the deterministic
    chore commit. When that resolved state also differs from
    ``head_sha`` (the originally committed blob), the user must
    have dirtied the path after the capture, and the transition
    must be SKIPPED to keep the chore commit isolated.

    The helper resolves ``rel_path`` through the filesystem, hashes
    the resolved file's bytes, and returns the git-blob SHA. Returns
    ``None`` for missing / unreadable / non-regular resolved paths
    so the caller can treat the result as "indeterminate" rather
    than "equal to HEAD".

    Args:
        repo: An open :class:`git.Repo` for the working tree.
        rel_path: Repo-relative path of the descendant whose
            resolved on-disk state we want to inspect.

    Returns:
        A 40-char hex git blob SHA, or ``None`` when the path
        cannot be resolved to a readable regular file.
    """
    try:
        working_dir = repo.working_dir
        if not working_dir:
            return None
        resolved = (Path(working_dir) / rel_path).resolve(strict=False)
        if not resolved.is_file():
            return None
        hashed: object = repo.git.hash_object(resolved)
    except (GitCommandError, OSError, ValueError):
        return None
    if not isinstance(hashed, str):
        return None
    return hashed.strip() or None


def capture_pre_write_contents(
    repo_root: Path | str,
    paths: list[str] | tuple[str, ...],
) -> dict[str, str | None]:
    """Record the git blob hash of every path BEFORE the deterministic write.

    This is the producer-side helper every deterministic writer
    (skill sync, ``.gitignore`` seed, policy preflight) MUST call
    immediately before writing, then pass the returned dict as
    ``pre_contents`` to :func:`commit_deterministic_writes`.

    The dict maps each path to its current git blob SHA-1 (the same
    value HEAD records), or ``None`` for a path that does not exist
    on disk yet. Symlinks are handled by ``git hash-object``.

    The helper is a thin ``git hash-object`` wrapper; it never raises.
    A git error or OSError yields ``None`` for the offending path so
    :func:`commit_deterministic_writes` will SKIP it (paths without a
    recorded pre-write hash are NOT committed — see its docstring).
    """
    recorded: dict[str, str | None] = {}
    try:
        repo = Repo(Path(repo_root))
    except (InvalidGitRepositoryError, Exception):
        # Non-git workspace -- record every path as None so the
        # downstream commit helper will skip them all (NOT_REPO at
        # the commit step will return a NOOP / NOT_REPO result).
        for path in paths:
            recorded[path] = None
        return recorded
    try:
        for path in paths:
            recorded[path] = _git_blob_sha(repo, path)
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()
    return recorded


# ---- classify_target_for_commit ---------------------------------------------

TargetCommitClass = Literal["tracked", "untracked_ignored", "untracked_new", "not_repo"]


# ---- classify_target_for_commit ----------------------------------------------


def _classify_untracked_target(repo: Repo, rel_path: str) -> TargetCommitClass | None:
    """Classify a target that is not in the index (the untracked probe).

    Returns the classification, or ``None`` when a probe failed closed:
    a genuine git error from ``ls-files`` / ``check-ignore`` means the
    target's index or ignore state is unprovable, so the caller must not
    trust the commit helper to stage only the right paths and treats the
    site as ``not_repo`` (skip silently).
    """
    try:
        repo.git.ls_files("--error-unmatch", "--", rel_path)
        return "tracked"
    except GitCommandError as exc:
        if exc.status != 1:
            logger.debug(
                "classify_target_for_commit: ls-files failed for {} ({}); treating as not_repo",
                rel_path,
                exc.status,
            )
            return None
    try:
        # ``--quiet`` suppresses output; the exit status carries the
        # answer (0 = ignored, 1 = not ignored, 128 = error).
        repo.git.check_ignore("--quiet", rel_path)
        return "untracked_ignored"
    except GitCommandError as exc:
        if exc.status == 1:
            # Not ignored -- a brand-new untracked path that the
            # deterministic writer is wholly authoring.
            return "untracked_new"
        # Any other status (128 = error) is fail-closed: an unprovable
        # ignore status cannot be trusted to commit on. Skip silently
        # rather than misclassifying a broken repo as ``untracked_new``.
        logger.debug(
            "classify_target_for_commit: check-ignore failed for {} ({}); treating as not_repo",
            rel_path,
            exc.status,
        )
    return None


def classify_target_for_commit(repo_root: Path | str, path: Path | str) -> TargetCommitClass:
    """Classify a filesystem target BEFORE a deterministic writer touches it.

    wt-12: ``config/bootstrap.py::_copy_with_backup`` needs to know, per
    config write, whether the target is tracked at HEAD, a new untracked
    path, an ignored path, or outside any repository -- so only
    Ralph-authored writes to tracked or genuinely-new paths route through
    :func:`commit_deterministic_writes`, while ignored targets and non-repo
    targets bypass commit handling silently.

    The containing repository is discovered from ``path`` itself (with
    ``search_parent_directories=True``) -- NEVER from the process CWD, which
    may be a different worktree.

    Classification:

    * ``tracked`` -- the path is tracked in the index (``git ls-files
      --error-unmatch`` succeeds);
    * ``untracked_ignored`` -- untracked AND ignored (``git check-ignore``
      succeeds);
    * ``untracked_new`` -- untracked and not ignored (a brand-new path a
      deterministic writer is wholly authoring);
    * ``not_repo`` -- the path is not inside a git working tree, the
      containing repo could not be discovered, or any classification
      probe (``ls-files`` / ``check-ignore``) returned a git error.
      The caller skips commit handling silently (no commit, no warning).

    Repository discovery uses the closest existing ancestor of
    ``path`` -- never ``path`` itself when it does not exist yet
    (the canonical first-creation case). ``Repo(nonexistent, ...)``
    raises ``NoSuchPathError`` even with
    ``search_parent_directories=True``, so a brand-new
    ``.agent/ralph-workflow.toml`` would otherwise misclassify as
    ``not_repo`` and the deterministic commit routing would skip
    it (DA-001 / DA-007 / DA-008 / DA-010 wt-12).
    """
    del repo_root  # the containing repo is discovered from ``path`` itself
    target = Path(path).resolve(strict=False)
    # Find the closest existing ancestor for the Repo() probe. Without
    # this, ``Repo(target, search_parent_directories=True)`` raises
    # ``NoSuchPathError`` when ``target`` itself does not exist on disk
    # yet -- the canonical first-creation shape -- and we wrongly
    # classify the path as ``not_repo``.
    repo_anchor = (
        target if target.exists() else target.parent
    )  # filesystem-read-ok: git boundary probe -- the repo root must be found before FileBackend seams are importable
    while (
        not repo_anchor.exists()
    ):  # filesystem-read-ok: same git-discovery probe, closest existing ancestor walk
        repo_anchor = repo_anchor.parent
    try:
        repo = Repo(repo_anchor, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError, OSError, ValueError):
        return "not_repo"
    try:
        working_dir = repo.working_dir
        if not working_dir:
            return "not_repo"
        try:
            rel_path = target.relative_to(Path(working_dir)).as_posix()
        except ValueError:
            return "not_repo"
        # ``None`` means the untracked probes failed closed (unprovable
        # index/ignore state) -- skip silently as ``not_repo``.
        untracked_class = _classify_untracked_target(repo, rel_path)
        if untracked_class is not None:
            return untracked_class
        return "not_repo"
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()


# ---- commit_deterministic_writes --------------------------------------------


def commit_deterministic_writes(  # noqa: PLR0911, PLR0912, PLR0915
    # The complexity guards (PLR0911/0912/0915) are opted out here:
    # the explicit-outcome contract (CREATED / NOOP / NOT_REPO /
    # FAILED / SKIPPED) plus the pre-write hash discipline and
    # failed-attempt rollback require more branches / returns /
    # statements than the cap. Splitting the helper would scatter
    # the rollback guarantee across files; a single function with
    # the full state machine is the documented contract.
    repo_root: Path | str,
    *,
    paths: tuple[str, ...] | list[str],
    pre_contents: Mapping[str, str | None],
    subject: str,
    create_commit_fn: CreateCommitFn,
    stage_fn: Callable[[Path | str, list[str]], None],
    body_builder: Callable[[list[str]], str] | None = None,
    intentional_transitions: frozenset[str] | None = None,
) -> ScopedCommitResult:
    """Commit EXACTLY the given paths, but only those whose HEAD blob hash
    equals the caller-recorded pre-write content hash.

    This is the single isolation primitive every deterministic background
    writer (skill sync, ``.gitignore`` seed, policy preflight) routes
    through. The contract:

    * ``paths`` is the byte-exact set of paths the deterministic writer
      just touched. Paths outside this set are NEVER staged.
    * ``pre_contents`` maps each path to its pre-write content hash
      (``None``/absent means "the path did not exist before the write").
      The recorded hash is the SHA-256 hex of the on-disk file content
      the writer saw BEFORE writing. ``commit_deterministic_writes``
      recomputes the HEAD blob hash and compares; a mismatch means the
      user or agent edited the file in between the writer's pre-write
      snapshot and the commit attempt. Such paths are SKIPPED with a
      warning so they stay in the agent / user commit flow and never
      enter a fixed-message deterministic commit.
    * ``subject`` is the literal conventional-commit subject line (e.g.
      ``chore(skills): sync baseline bundle``).
    * ``create_commit_fn`` and ``stage_fn`` are the production
      dependencies. Tests inject stubs / spies.
    * ``intentional_transitions`` (optional) is the set of paths
      whose ancestor-symlink transition the caller has AUTHORED
      (e.g. the install code's sibling-root symlink). When a path's
      ancestor is a symlink (the ``_ANCESTOR_SYMLINK_DIRTY``
      shape) the on-disk state cannot be cheaply compared against
      HEAD, and the resolved state may legitimately differ from
      HEAD because the install set the canonical's content
      intentionally. Listing the transition's root path here
      bypasses the wt-012 DA-003/DA-010/DA-012 user-dirty
      descendant check for that path -- the caller has confirmed
      the transition is intentional, so a non-matching resolved
      state is the install's content, not a user edit. The
      bypass is per-transition-root, not per-descendant.

    Outcomes:

    * ``CREATED`` -- at least one in-scope path was committed. ``sha``
      is the commit SHA.
    * ``NOOP`` -- every in-scope path's post-write content still equals
      HEAD (nothing changed since the writer ran, or the writer's
      write was a no-op idempotent rewrite of the existing blob).
    * ``SKIPPED`` -- at least one in-scope path was already dirty at
      HEAD (HEAD != pre-write hash), or an ancestor transition stage
      (dir→symlink replacement) would sweep such a skipped
      descendant's deletion into the commit; git cannot hold the new
      symlink and the kept descendant in one tree, so the ancestor and
      every stageable entry beneath it join ``skipped_paths`` instead
      (wt-012 DA-003/DA-008, see ``ralph.git._transition_guard``).
      Skipped paths are left dirty for the agent flow. Other in-scope
      paths are still committed.
    * ``NOT_REPO`` -- ``repo_root`` is not a git working tree.
    * ``FAILED`` -- any ``OSError`` / ``GitCommandError`` from the
      attempt. The pre-attempt index state is restored byte-for-byte
      before the helper returns so the failed commit leaves no
      half-staged debris.
    """
    path_list: list[str] = sorted({p for p in paths if p})
    if not path_list:
        return ScopedCommitResult(status=ScopedCommitStatus.NOOP)

    try:
        repo_root_path = Path(repo_root)
        try:
            repo = Repo(repo_root_path)
        except (InvalidGitRepositoryError, Exception):
            logger.debug(
                "commit_deterministic_writes: {} is not a git repo; skipping",
                repo_root_path,
            )
            return ScopedCommitResult(status=ScopedCommitStatus.NOT_REPO)

        # wt-012 DA-006: an unborn HEAD (no commits yet) makes every
        # HEAD probe raise ValueError mid-attempt -- after staging -- and
        # the guards below catch only OSError/GitCommandError, so the
        # failure escaped as an unhandled exception with a half-staged
        # index. Fail closed HERE, before any snapshot or staging, so
        # the index is untouched and the caller gets an explicit FAILED.
        try:
            _ = repo.head.commit
        except ValueError as unborn_exc:
            logger.warning(
                "commit_deterministic_writes: {} has an unborn HEAD "
                "(no commits yet); refusing the deterministic commit",
                repo_root_path,
            )
            return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(unborn_exc))

        try:
            # Snapshot the FULL pre-staged state (in-scope and out-of-scope)
            # so the failed-attempt rollback can restore byte-for-byte. We
            # will unstage the whole set, stage ONLY the in-scope paths
            # the deterministic writer touched, attempt the commit, then
            # restore.
            pre_staged_paths = _capture_staged_paths(repo)
            pre_staged_snapshots = _snapshot_pre_staged_index(repo, pre_staged_paths)
            # Unstage EVERY pre-staged path so the chore commit cannot
            # capture unrelated pre-staged entries.
            if pre_staged_paths:
                _ = cast("None", repo.git.reset("HEAD", "--", *pre_staged_paths))

            try:
                # Compare each in-scope path's HEAD blob hash against the
                # caller-recorded pre-write content hash. SKIPPED paths
                # stay dirty for the agent flow.
                stageable: list[str] = []
                skipped: list[str] = []
                for path in path_list:
                    head_sha = _read_head_blob_sha(repo, path)
                    pre_sha = pre_contents.get(path)
                    if head_sha == _HEAD_PROBE_FAILED:
                        # HEAD metadata probe failed -- cannot distinguish
                        # "absent at HEAD" from a broken index. Fail closed:
                        # stage NOTHING and report FAILED (wt-012 DA-006/
                        # DA-011). The finally below restores the pre-staged
                        # snapshot, so the index stays byte-for-byte intact.
                        logger.warning(
                            "commit_deterministic_writes: HEAD metadata probe failed "
                            "for {}; refusing to stage the deterministic commit",
                            path,
                        )
                        return ScopedCommitResult(
                            status=ScopedCommitStatus.FAILED,
                            skipped_paths=tuple(skipped),
                            error=(
                                "HEAD metadata probe failed; refusing to stage deterministic commit"
                            ),
                        )
                    if pre_sha is None and head_sha is None:
                        # Brand-new path: absent on disk at pre-write capture
                        # AND confirmed absent at HEAD -- the deterministic
                        # writer authored the whole file, so committing it
                        # cannot sweep in anyone else's work (wt-012 DA-006).
                        # wt-012 DA-007/DA-012: a brand-new path that sits
                        # under a newly-installed symlink ancestor (e.g. a
                        # ``shutil.copytree`` fallback that materialized a
                        # leaf under a sibling root now backed by a
                        # symlink) cannot be staged as its lexical path --
                        # ``git add --all`` would fatal with ``pathspec ...
                        # is beyond a symbolic link``. Rewrite the
                        # brand-new path to the symlink ancestor so the
                        # new symlink and the materialized leaves land
                        # atomically in one commit.
                        if _has_symlink_ancestor(Path(repo.working_dir) / path):
                            ancestor_path = _symlink_ancestor_path(repo, path)
                            if ancestor_path is not None:
                                logger.debug(
                                    "commit_deterministic_writes: brand-new path {} "
                                    "is beyond a symlink; rewriting to ancestor {} for "
                                    "atomic staging",
                                    path,
                                    ancestor_path,
                                )
                                stageable.append(ancestor_path)
                                continue
                        stageable.append(path)
                        continue
                    if pre_sha is None:
                        # Caller did not record a pre-write hash for this
                        # path (or the file existed at HEAD but was missing
                        # on disk at capture time). Treat as suspicious --
                        # the deterministic writer should know what every
                        # path's prior content was. SKIP rather than commit
                        # a path we cannot isolate.
                        logger.warning(
                            "commit_deterministic_writes: path {} has no pre-write hash "
                            "recorded; skipping to avoid sweeping in unrelated changes",
                            path,
                        )
                        skipped.append(path)
                        continue
                    if head_sha != pre_sha:
                        # HEAD does not match the caller's pre-write hash:
                        # the user or agent dirtied the file in between
                        # the writer's snapshot and the commit attempt.
                        # SKIP so the chore commit cannot ride their work.
                        logger.warning(
                            "commit_deterministic_writes: path {} was already dirty at HEAD "
                            "(head_sha != pre_sha); skipping to keep the chore commit isolated",
                            path,
                        )
                        skipped.append(path)
                        continue
                    # HEAD matches the pre-write hash, so the on-disk
                    # content the writer just produced is the only diff
                    # for this path since HEAD. Confirm the on-disk
                    # content also differs from HEAD (otherwise nothing
                    # to commit). wt-012 PA-002: an ancestor-symlink
                    # shape (a deleted sibling dir replaced by a
                    # symlink that resolves to a same-named same-content
                    # file) makes ``git hash-object`` return a value
                    # that the cheap ``== head_sha`` comparison cannot
                    # distinguish from "no real change" -- the
                    # ``_ANCESTOR_SYMLINK_DIRTY`` sentinel forces the
                    # path into ``stageable`` instead.
                    on_disk_sha = _git_blob_sha(repo, path)
                    if on_disk_sha == _ANCESTOR_SYMLINK_DIRTY:
                        # The path is lexically unreachable from HEAD
                        # (an ancestor is a symlink to a target that no
                        # longer contains this file). Replacing the
                        # descendant with its nearest symlink ancestor
                        # in the stageable set lets ``git add --all``
                        # pick up both the new symlink and the
                        # descendant deletions atomically -- without
                        # this rewrite ``git add`` would fatal with
                        # ``pathspec ... is beyond a symbolic link``.
                        ancestor_path = _symlink_ancestor_path(repo, path)
                        if ancestor_path is not None:
                            # wt-012 DA-003/DA-010/DA-012: a user change
                            # that lands BETWEEN the producer's pre-write
                            # capture and the install makes the recorded
                            # ``pre_sha`` stale, and the cheap
                            # HEAD-vs-pre-sha equality check above does
                            # not catch it. When the resolved on-disk
                            # state (the file the symlink target now
                            # exposes) also differs from ``head_sha``,
                            # the user must have dirtied the descendant
                            # AFTER the capture, and committing the
                            # ancestor transition would sweep the user's
                            # deletion into the chore commit. Mark the
                            # descendant as SKIPPED so
                            # ``drop_transition_conflicts`` drops the
                            # ancestor too. The caller can opt out via
                            # ``intentional_transitions`` when the
                            # transition's resolved content is the
                            # install's own canonical target.
                            if (
                                intentional_transitions is None
                                or ancestor_path not in intentional_transitions
                            ):
                                resolved_sha = _resolved_descendant_sha(repo, path)
                                if resolved_sha is not None and resolved_sha != head_sha:
                                    logger.warning(
                                        "commit_deterministic_writes: path {} "
                                        "resolves through a new symlink ancestor to "
                                        "a state that differs from HEAD; the user "
                                        "dirtied this descendant after the "
                                        "pre-write snapshot, skipping to keep the "
                                        "chore commit isolated",
                                        path,
                                    )
                                    skipped.append(path)
                                    del on_disk_sha
                                    continue
                            logger.debug(
                                "commit_deterministic_writes: path {} is beyond a symlink; "
                                "rewriting to ancestor {} for atomic staging",
                                path,
                                ancestor_path,
                            )
                            stageable.append(ancestor_path)
                            del on_disk_sha  # narrow explicit type for the next iter
                            continue
                    if on_disk_sha is not None and on_disk_sha == head_sha:
                        # No actual change since HEAD -- skip.
                        continue
                    stageable.append(path)
                    del on_disk_sha  # narrow explicit type for the next iter

                # Dedupe (a dir→symlink rewrite can append the same
                # ancestor once per descendant plus once for the root).
                # wt-012 DA-003/DA-008: drop ancestors whose stage would
                # sweep a SKIPPED dirty descendant's deletion.
                stageable = sorted(set(stageable))
                if skipped and stageable:
                    stageable = drop_transition_conflicts(stageable, skipped)

                if not stageable:
                    return ScopedCommitResult(
                        status=ScopedCommitStatus.SKIPPED if skipped else ScopedCommitStatus.NOOP,
                        skipped_paths=tuple(skipped),
                    )

                # Stage EXACTLY the stageable set via the injected
                # stage_fn so the production git plumbing is the same
                # code path the rest of the pipeline uses. wt-012 DA-006:
                # a partial staging failure must NOT leave the newly
                # staged set half-applied, so the stage call is inside
                # the same try that owns the failed-attempt rollback.
                try:
                    stage_fn(repo_root_path, stageable)
                except (OSError, GitCommandError):
                    _ = cast("None", repo.git.reset("HEAD", "--", *stageable))
                    raise
                try:
                    if body_builder is not None:
                        body = body_builder(stageable)
                    else:
                        body_lines = [
                            "Auto-generated by Ralph deterministic writer",
                            "",
                            "Changed files:",
                            *(f"- {p}" for p in stageable),
                        ]
                        body = "\n".join(body_lines)
                    message = f"{subject}\n\n{body}"
                    expected_head = str(repo.head.commit.hexsha)
                    result = create_commit_fn(repo_root_path, message, expected_head=expected_head)
                    if result.status is not CommitCreationStatus.CREATED or result.sha is None:
                        # Failed commit -- rollback the stage we just did
                        # and restore the pre-staged snapshot. NO half
                        # staged index.
                        _ = cast(
                            "None",
                            repo.git.reset("HEAD", "--", *stageable),
                        )
                        _restore_pre_staged_index(repo, pre_staged_snapshots)
                        error_message: str | None
                        error_attr: object = getattr(result, "error", None)
                        if error_attr is not None:
                            error_message = str(error_attr)
                        else:
                            error_message = "create_commit did not return CREATED"
                        return ScopedCommitResult(
                            status=ScopedCommitStatus.FAILED,
                            skipped_paths=tuple(skipped),
                            error=error_message,
                        )
                    return ScopedCommitResult(
                        status=ScopedCommitStatus.CREATED,
                        sha=result.sha,
                        skipped_paths=tuple(skipped),
                    )
                except (OSError, GitCommandError) as inner_exc:
                    # Commit raised -- rollback the stage and restore the
                    # pre-staged snapshot so the failed attempt leaves no
                    # half-staged debris.
                    reset_error: str | None = None
                    try:
                        _ = cast(
                            "None",
                            repo.git.reset("HEAD", "--", *stageable),
                        )
                    except (OSError, GitCommandError) as reset_exc:  # pragma: no cover
                        # wt-012 DA-006: a failed rollback reset can leave
                        # the newly staged paths in the index. Surface the
                        # dirty-index state in the FAILED error so callers
                        # know manual repair may be needed.
                        reset_error = str(reset_exc)
                        logger.warning(
                            "commit_deterministic_writes: failed-attempt unstage failed; "
                            "newly staged paths may remain in the index: {}",
                            reset_exc,
                        )
                    _restore_pre_staged_index(repo, pre_staged_snapshots)
                    error_detail = str(inner_exc)
                    if reset_error is not None:
                        error_detail = f"{error_detail}; rollback reset failed: {reset_error}"
                    return ScopedCommitResult(
                        status=ScopedCommitStatus.FAILED,
                        skipped_paths=tuple(skipped),
                        error=error_detail,
                    )
            finally:
                # Always restore the pre-staged snapshot (modulo any
                # rollback above) so the user's staged state is preserved
                # byte-for-byte after the deterministic commit succeeds.
                # wt-012 DA-006: a failed restoration must not be silently
                # swallowed while the helper still reports CREATED -- the
                # user's pre-staged state is lost, so the commit cannot be
                # considered fully successful. Re-raise so the outer guard
                # converts it to an explicit FAILED outcome.
                if pre_staged_snapshots:
                    _restore_pre_staged_index(repo, pre_staged_snapshots)
        finally:
            close = cast("Callable[[], object] | None", getattr(repo, "close", None))
            if callable(close):
                close()
    except (OSError, GitCommandError, ValueError) as exc:
        logger.warning("commit_deterministic_writes: outer guard caught (non-fatal): {}", exc)
        return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))


# ---- commit_scoped_updates (legacy public helper) ---------------------------


def commit_scoped_updates(  # noqa: PLR0912
    # The complexity guards (PLR0911/PLR0912) are opted out: the
    # explicit-outcome contract (CREATED / NOOP / NOT_REPO / FAILED) plus the
    # staged-state preservation requires more branches than the cap.
    # Splitting the helper would scatter the snapshot / restore
    # discipline across files; a single function is the contract.
    repo_root: Path | str,
    *,
    scopes: tuple[str, ...],
    subject: str,
    body_builder: Callable[[list[str]], str],
    create_commit_fn: CreateCommitFn,
    stage_fn: Callable[[Path | str, list[str]], None],
    path_filter: Callable[[str], bool] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> ScopedCommitResult:
    """Create one deterministic chore commit for dirty paths inside ``scopes``.

    ``path_filter`` (optional) further narrows the in-scope dirty set — a
    path is committed only when the filter returns True. Callers use it for
    content-conditional scopes (e.g. commit a migration candidate only when
    it carries the migrated marker).

    ``exclude`` is subtracted from the dirty set BEFORE anything else. Callers
    pass the paths that were already dirty before the automated work began, so a
    file the user was mid-edit on is never swept into an automated commit — even
    when it sits inside one of the ``scopes``. Being Ralph-owned (``AGENTS.md``,
    the canonical policy dir) makes a file in-scope; it does NOT make the user's
    uncommitted edit to it ours to commit.

    Returns a :class:`ScopedCommitResult` describing the outcome. On
    ``CREATED`` the SHA is in ``result.sha``; on every other status
    ``result.sha`` is ``None``.
    """
    try:
        repo_root_path = Path(repo_root)
        try:
            repo = Repo(repo_root_path)
        except (InvalidGitRepositoryError, Exception):
            logger.debug(
                "commit_scoped_updates: {} is not a git repo; skipping auto-commit",
                repo_root_path,
            )
            return ScopedCommitResult(status=ScopedCommitStatus.NOT_REPO)

        # wt-012 DA-006: an unborn HEAD raises ValueError from the HEAD
        # probe AFTER staging; the inner rollback below unstages the set
        # and the outer guard (ValueError included) reports FAILED.

        try:
            all_dirty: list[str] = []
            for scope in sorted(scopes):
                all_dirty.extend(_list_dirty_paths(repo, scope))
            # Defensive re-filter: drop anything outside the scopes even if
            # ``git status -- <scope>`` somehow returned it. Then drop everything
            # that was already dirty before we started -- that is the user's work.
            all_dirty = sorted(
                {path for path in all_dirty if path_in_scope(path, scopes) and path not in exclude}
            )
            if path_filter is not None:
                all_dirty = [path for path in all_dirty if path_filter(path)]
            if not all_dirty:
                return ScopedCommitResult(status=ScopedCommitStatus.NOOP)
            # Snapshot and unstage every pre-staged path (in-scope and
            # out-of-scope) so the chore commit cannot capture unrelated
            # pre-staged entries, then restore the exact index state
            # afterwards including staged deletions.
            pre_staged_paths = _capture_staged_paths(repo)
            pre_staged_blobs = _snapshot_pre_staged_index(repo, pre_staged_paths)
            if pre_staged_paths:
                _ = cast("None", repo.git.reset("HEAD", "--", *pre_staged_paths))
            try:
                stage_fn(repo_root_path, all_dirty)
                message = f"{subject}\n\n{body_builder(all_dirty)}"
                expected_head = str(repo.head.commit.hexsha)
                result = create_commit_fn(repo_root_path, message, expected_head=expected_head)
                if result.status is not CommitCreationStatus.CREATED or result.sha is None:
                    # Commit failed: rollback the stage, restore the
                    # pre-staged snapshot, return FAILED.
                    try:
                        _ = cast("None", repo.git.reset("HEAD", "--", *all_dirty))
                    except (OSError, GitCommandError) as reset_exc:  # pragma: no cover
                        logger.debug(
                            "commit_scoped_updates: failed-attempt unstage failed (non-fatal): {}",
                            reset_exc,
                        )
                    _restore_pre_staged_index(repo, pre_staged_blobs)
                    scope_error_attr: object = getattr(result, "error", None)
                    if scope_error_attr is not None:
                        scope_error_message: str | None = str(scope_error_attr)
                    else:
                        scope_error_message = "create_commit did not return CREATED"
                    return ScopedCommitResult(
                        status=ScopedCommitStatus.FAILED,
                        error=scope_error_message,
                    )
                return ScopedCommitResult(status=ScopedCommitStatus.CREATED, sha=result.sha)
            except (OSError, GitCommandError, ValueError):
                # wt-012 DA-006: a raised commit failure (including the
                # unborn-HEAD ValueError) must unstage the
                # freshly staged set before the finally below restores the
                # pre-staged snapshot -- otherwise the failure leaves the
                # deterministic paths half-staged for a later agent commit.
                try:
                    _ = cast("None", repo.git.reset("HEAD", "--", *all_dirty))
                except (OSError, GitCommandError, ValueError) as reset_exc:  # pragma: no cover
                    logger.warning(
                        "commit_scoped_updates: failed-attempt unstage failed "
                        "(index may need manual repair): {}",
                        reset_exc,
                    )
                raise
            finally:
                # Best-effort restore -- a broken git state MUST NOT block
                # the pipeline. Worst case the user re-runs ``git add``.
                if pre_staged_blobs:
                    try:
                        _restore_pre_staged_index(repo, pre_staged_blobs)
                    except (OSError, GitCommandError) as restore_exc:  # pragma: no cover
                        # wt-012 DA-003: a failed restoration silently loses
                        # the user's pre-staged state while the helper still
                        # reports success. Report it at WARNING so the loss
                        # is visible at normal verbosity.
                        logger.warning(
                            "commit_scoped_updates: failed to restore pre-staged "
                            "paths (user's staged state may need manual repair): {}",
                            restore_exc,
                        )
        except (OSError, GitCommandError) as exc:
            logger.warning("commit_scoped_updates: auto-commit failed (non-fatal): {}", exc)
            return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))
        finally:
            close = cast("Callable[[], object] | None", getattr(repo, "close", None))
            if callable(close):
                close()
    except (OSError, GitCommandError, ValueError) as exc:
        logger.warning("commit_scoped_updates: outer guard caught (non-fatal): {}", exc)
        return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))


__all__ = [
    "CreateCommitFn",
    "ScopedCommitResult",
    "ScopedCommitStatus",
    "TargetCommitClass",
    "capture_pre_write_contents",
    "commit_deterministic_writes",
    "commit_scoped_updates",
    "list_dirty_paths",
    "path_in_scope",
    "snapshot_dirty_paths_strict",
]
