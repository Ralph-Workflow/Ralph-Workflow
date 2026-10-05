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

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import GitCommandError, InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol

    class _CreateCommitFn(Protocol):
        def __call__(
            self, repo_root: Path | str, message: str, *, expected_head: str
        ) -> CommitCreationResult: ...


# ``git status --porcelain`` lines start with a 2-char status code followed
# by a single space -- so a valid line has at least 4 characters of prefix
# before the path body begins. Lines shorter than this are noise.
_GIT_PORCELAIN_PREFIX_LEN: int = 4

# ``git ls-files --stage`` returns ``<mode> <blob-sha> <stage>\t<path>``.
_GIT_LS_FILES_META_FIELDS: int = 3
_GIT_LS_FILES_PATH_PARTS: int = 2

# Stage-0 means "fully merged in the index". Anything else indicates an
# unmerged conflict state which the chore commit MUST NOT touch.
_GIT_INDEX_STAGE_MERGED: str = "0"


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
    SKIPPED = "skipped"  # Caller asked to commit, but every in-scope path was already dirty at HEAD.


@dataclass(frozen=True)
class ScopedCommitResult:
    """Typed outcome for a scoped deterministic commit attempt.

    Attributes:
        status: Outcome category (CREATED / NOOP / NOT_REPO / FAILED / SKIPPED).
        sha: The commit SHA on ``CREATED``, else ``None``.
        skipped_paths: Paths the caller asked to commit whose pre-write
            content hash did not match HEAD (they were already dirty
            before the deterministic writer touched them and so were
            left for the agent flow). Empty on every non-SKIPPED status.
        error: Human-readable failure detail for ``FAILED``; ``None``
            otherwise.
    """

    status: ScopedCommitStatus
    sha: str | None = None
    skipped_paths: tuple[str, ...] = ()
    error: str | None = None


def path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    """True when ``path`` falls under any scope (dir prefix or exact file)."""
    return any(path.startswith(scope) if scope.endswith("/") else path == scope for scope in scopes)


def _list_dirty_paths(repo: Repo, scope: str) -> list[str]:
    """Return sorted repo-relative dirty paths under one scope.

    ``--untracked-files=all`` is required so nested untracked files are
    reported individually rather than collapsed to the parent directory.
    """
    raw = cast(
        "str", repo.git.status("--porcelain", "--untracked-files=all", "--", scope)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    dirty: list[str] = []
    for line in raw.splitlines():
        if len(line) < _GIT_PORCELAIN_PREFIX_LEN:
            continue
        path = line[_GIT_PORCELAIN_PREFIX_LEN - 1 :].strip()
        if path_in_scope(path, (scope,)):
            dirty.append(path)
    return sorted(set(dirty))


def list_dirty_paths(repo_root: Path | str) -> frozenset[str]:
    """Return every dirty repo-relative path in the working tree.

    Used to snapshot the working tree BEFORE an agent runs, so a later call can
    attribute the newly-dirty paths to that agent and commit only those. A path
    the user had already modified is never swept into an automated commit.

    Returns an empty set for a non-git workspace or on any git error: a failure
    to read the tree must degrade to "attribute nothing", never to "attribute
    everything".
    """
    try:
        repo = Repo(Path(repo_root), search_parent_directories=False)
        raw = cast("str", repo.git.status("--porcelain", "--untracked-files=all"))
    except (InvalidGitRepositoryError, GitCommandError, OSError) as exc:
        logger.debug("could not snapshot the working tree ({}); assuming clean", exc)
        return frozenset()
    return frozenset(
        line[_GIT_PORCELAIN_PREFIX_LEN - 1 :].strip()
        for line in raw.splitlines()
        if len(line) >= _GIT_PORCELAIN_PREFIX_LEN
    )


def snapshot_dirty_paths_strict(repo_root: Path | str) -> frozenset[str] | None:
    """Snapshot the working tree, distinguishing clean from unreadable.

    Returns:

    * ``frozenset()`` when the working tree is clean (no dirty paths).
    * ``frozenset({...})`` listing the dirty paths.
    * ``None`` when the tree could not be read (non-git workspace, git
      error, OSError). Callers use ``None`` to skip a deterministic
      chore commit with a visible warning, instead of the legacy
      "degrade to empty" path that conflated clean with broken.

    Mirrors :func:`list_dirty_paths` on the read path; the difference is
    the return type (``None`` vs empty ``frozenset``) so callers can
    decide whether to skip with a warning.
    """
    try:
        repo = Repo(Path(repo_root), search_parent_directories=False)
        raw = cast("str", repo.git.status("--porcelain", "--untracked-files=all"))
    except (InvalidGitRepositoryError, GitCommandError, OSError) as exc:
        logger.debug("could not snapshot the working tree strictly ({}); reporting None", exc)
        return None
    return frozenset(
        line[_GIT_PORCELAIN_PREFIX_LEN - 1 :].strip()
        for line in raw.splitlines()
        if len(line) >= _GIT_PORCELAIN_PREFIX_LEN
    )


# Sentinel returned by :func:`_read_head_blob_sha` when the HEAD metadata
# probe itself failed (``GitCommandError``). MUST be distinguishable from
# ``None`` ("absent at HEAD") so a broken index can never masquerade as a
# brand-new path and authorize staging (wt-012 DA-006/DA-011: fail closed).
_HEAD_PROBE_FAILED: str = "__HEAD_PROBE_FAILED__"


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
        raw = cast("str", repo.git.ls_files("--stage", "--", path))
    except GitCommandError:
        return _HEAD_PROBE_FAILED
    if not raw.strip():
        return None
    for line in raw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != _GIT_LS_FILES_PATH_PARTS:
            continue
        meta = parts[0].split()
        if len(meta) < _GIT_LS_FILES_META_FIELDS:
            continue
        return cast("str | None", meta[1])
    return None


def _git_blob_sha(repo: Repo, rel_path: str) -> str | None:
    """Return the git blob SHA-1 of the on-disk content at ``rel_path``.

    Uses ``git hash-object <path>`` so the value is git's blob hash,
    matching what :func:`_read_head_blob_sha` returns for HEAD. This
    makes the caller's recorded pre-write hash directly comparable
    to HEAD (a deterministic writer passes pre-write = ``git
    hash-object`` of the file as it stood BEFORE the write). Symlinks
    are handled natively: git stores them as blobs containing the link
    target, so the on-disk and HEAD blob hashes are the same shape.

    Returns ``None`` for unreadable / missing / non-git paths.
    """
    try:
        working_dir = repo.working_dir
    except (OSError, ValueError):
        return None
    if not working_dir:
        return None
    abs_path = Path(working_dir) / rel_path
    try:
        return cast("str", repo.git.hash_object(abs_path)).strip() or None
    except (GitCommandError, OSError):
        return None


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
        return {p: None for p in paths}  # noqa: C420  # ruff prefers dict.fromkeys; mypy loses the literal type on it
    try:
        for path in paths:
            recorded[path] = _git_blob_sha(repo, path)
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()
    return recorded


# ---- Pre-staged index snapshot / restore (handles staged deletions) ---------

# Sentinel: a path was in ``git diff --cached --name-only`` but absent
# from ``git ls-files --stage`` -> the user staged a deletion. Restore
# by ``git update-index --force-remove <path>`` to keep the index and
# the file in sync with the pre-staged state.
_STAGED_DELETION_SENTINEL: str = "__STAGED_DELETION__"

# Sentinel: a path was in the index at pre-staged snapshot time. We
# must record its mode + blob to restore the byte-exact index entry.
# No marker in particular -- any non-sentinel value is fine.
_STAGED_ENTRY_NONE: str | None = None


def _snapshot_pre_staged_index(
    repo: Repo, paths: list[str]
) -> dict[str, str | None]:
    """Capture each pre-staged path's index entry, including staged deletions.

    A path the user pre-staged for a modification has an index entry
    ``<mode> <blob> <stage>\t<path>`` -- we record ``"<mode>,<blob>"``
    so the restore can replay it with ``git update-index --cacheinfo``.

    A path the user pre-staged for a deletion appears in
    ``git diff --cached --name-only`` but NOT in
    ``git ls-files --stage``. We record the
    :data:`_STAGED_DELETION_SENTINEL` so the restore knows to call
    ``git update-index --force-remove <path>`` -- without that call,
    the user's staged deletion would silently revert to the
    pre-deletion index entry after the chore commit.

    Unmerged (non-stage-0) entries are skipped defensively (a chore
    commit MUST NOT touch an unmerged index).
    """
    snapshots: dict[str, str | None] = {}
    if not paths:
        return snapshots
    ls_files_raw = cast(
        "str", repo.git.ls_files("--stage", "--", *paths)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    indexed: set[str] = set()
    for ls_line in ls_files_raw.splitlines():
        parts = ls_line.split("\t", 1)
        if len(parts) != _GIT_LS_FILES_PATH_PARTS:
            continue
        meta = parts[0].split()
        if len(meta) < _GIT_LS_FILES_META_FIELDS:
            continue
        mode, blob_sha, stage = meta[0], meta[1], meta[2]
        if stage != _GIT_INDEX_STAGE_MERGED:
            continue
        snapshots[parts[1]] = f"{mode},{blob_sha}"
        indexed.add(parts[1])
    # Any pre-staged path NOT in the index is a staged deletion.
    for path in paths:
        if path not in indexed:
            snapshots[path] = _STAGED_DELETION_SENTINEL
    return snapshots


def _restore_pre_staged_index(repo: Repo, snapshots: dict[str, str | None]) -> None:
    """Restore each pre-staged path's exact index state, including staged deletions.

    For an existing index entry, replay it via
    ``git update-index --add --cacheinfo <mode>,<blob>,<path>``. For a
    staged deletion (the :data:`_STAGED_DELETION_SENTINEL` value), force
    the path out of the index with ``git update-index --force-remove``.
    """
    for path, entry in snapshots.items():
        if entry == _STAGED_DELETION_SENTINEL:
            _ = cast("None", repo.git.update_index("--force-remove", path))
            continue
        if entry is None:
            continue
        mode, blob_sha = entry.split(",", 1)
        _ = cast(
            "None",
            repo.git.update_index(
                "--add",
                "--cacheinfo",
                f"{mode},{blob_sha},{path}",
            ),
        )


def _capture_staged_paths(repo: Repo) -> list[str]:
    """Return the list of pre-staged paths (``git diff --cached --name-only``)."""
    diff_output: object = repo.git.diff("--cached", "--name-only")
    if not isinstance(diff_output, str):
        return []
    return [path for path in diff_output.splitlines() if path]


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
    create_commit_fn: _CreateCommitFn,
    stage_fn: Callable[[Path | str, list[str]], None],
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

    Outcomes:

    * ``CREATED`` -- at least one in-scope path was committed. ``sha``
      is the commit SHA.
    * ``NOOP`` -- every in-scope path's post-write content still equals
      HEAD (nothing changed since the writer ran, or the writer's
      write was a no-op idempotent rewrite of the existing blob).
    * ``SKIPPED`` -- at least one in-scope path was already dirty at
      HEAD (HEAD != pre-write hash) so the caller's fixed-message
      commit would have swept in unrelated work. Those paths are
      SKIPPED and left dirty for the agent flow. Other in-scope paths
      are still committed; ``skipped_paths`` lists the SKIPPED ones.
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
                                "HEAD metadata probe failed; refusing to stage "
                                "deterministic commit"
                            ),
                        )
                    if pre_sha is None and head_sha is None:
                        # Brand-new path: absent on disk at pre-write capture
                        # AND confirmed absent at HEAD -- the deterministic
                        # writer authored the whole file, so committing it
                        # cannot sweep in anyone else's work (wt-012 DA-006).
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
                    # to commit).
                    on_disk_sha = _git_blob_sha(repo, path)
                    if on_disk_sha is not None and on_disk_sha == head_sha:
                        # No actual change since HEAD -- skip.
                        continue
                    stageable.append(path)
                    del on_disk_sha  # narrow explicit type for the next iter

                if not stageable:
                    return ScopedCommitResult(
                        status=ScopedCommitStatus.SKIPPED
                        if skipped
                        else ScopedCommitStatus.NOOP,
                        skipped_paths=tuple(skipped),
                    )

                # Stage EXACTLY the stageable set via the injected
                # stage_fn so the production git plumbing is the same
                # code path the rest of the pipeline uses.
                stage_fn(repo_root_path, stageable)
                try:
                    body_lines = [
                        "Auto-generated by Ralph deterministic writer",
                        "",
                        "Changed files:",
                        *(f"- {p}" for p in stageable),
                    ]
                    body = "\n".join(body_lines)
                    message = f"{subject}\n\n{body}"
                    expected_head = str(repo.head.commit.hexsha)
                    result = create_commit_fn(
                        repo_root_path, message, expected_head=expected_head
                    )
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
                    try:
                        _ = cast(
                            "None",
                            repo.git.reset("HEAD", "--", *stageable),
                        )
                    except (OSError, GitCommandError) as reset_exc:  # pragma: no cover
                        logger.debug(
                            "commit_deterministic_writes: failed-attempt unstage failed "
                            "(non-fatal): {}",
                            reset_exc,
                        )
                    _restore_pre_staged_index(repo, pre_staged_snapshots)
                    return ScopedCommitResult(
                        status=ScopedCommitStatus.FAILED,
                        skipped_paths=tuple(skipped),
                        error=str(inner_exc),
                    )
            finally:
                # Always restore the pre-staged snapshot (modulo any
                # rollback above) so the user's staged state is preserved
                # byte-for-byte after the deterministic commit succeeds.
                if pre_staged_snapshots:
                    try:
                        _restore_pre_staged_index(repo, pre_staged_snapshots)
                    except (OSError, GitCommandError) as restore_exc:  # pragma: no cover
                        logger.debug(
                            "commit_deterministic_writes: failed to restore pre-staged "
                            "paths (non-fatal): {}",
                            restore_exc,
                        )
        finally:
            close = cast("Callable[[], object] | None", getattr(repo, "close", None))
            if callable(close):
                close()
    except (OSError, GitCommandError) as exc:
        logger.debug("commit_deterministic_writes: outer guard caught (non-fatal): {}", exc)
        return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))


# ---- commit_scoped_updates (legacy public helper) ---------------------------


def commit_scoped_updates(  # noqa: PLR0912
    # The complexity guard (PLR0912) is opted out: the explicit-outcome
    # contract (CREATED / NOOP / NOT_REPO / FAILED) plus the
    # staged-state preservation requires more branches than the cap.
    # Splitting the helper would scatter the snapshot / restore
    # discipline across files; a single function is the contract.
    repo_root: Path | str,
    *,
    scopes: tuple[str, ...],
    subject: str,
    body_builder: Callable[[list[str]], str],
    create_commit_fn: _CreateCommitFn,
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
                            "commit_scoped_updates: failed-attempt unstage failed "
                            "(non-fatal): {}",
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
            finally:
                # Best-effort restore -- a broken git state MUST NOT block
                # the pipeline. Worst case the user re-runs ``git add``.
                if pre_staged_blobs:
                    try:
                        _restore_pre_staged_index(repo, pre_staged_blobs)
                    except (OSError, GitCommandError) as restore_exc:  # pragma: no cover
                        logger.debug(
                            "commit_scoped_updates: failed to restore pre-staged "
                            "paths (non-fatal): {}",
                            restore_exc,
                        )
        except (OSError, GitCommandError) as exc:
            logger.debug("commit_scoped_updates: auto-commit failed (non-fatal): {}", exc)
            return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))
        finally:
            close = cast("Callable[[], object] | None", getattr(repo, "close", None))
            if callable(close):
                close()
    except (OSError, GitCommandError) as exc:
        logger.debug("commit_scoped_updates: outer guard caught (non-fatal): {}", exc)
        return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))


__all__ = [
    "ScopedCommitResult",
    "ScopedCommitStatus",
    "capture_pre_write_contents",
    "commit_deterministic_writes",
    "commit_scoped_updates",
    "list_dirty_paths",
    "path_in_scope",
    "snapshot_dirty_paths_strict",
]
