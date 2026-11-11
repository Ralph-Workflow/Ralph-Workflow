"""Proposal seal and rollback primitives for commit review.

Provides mechanisms to seal pending working tree changes into an isolated
Git proposal ref using a temporary Git index without mutating the real index
or working tree. Supports tree diff inspection, preflight validation, proposal
pruning, dirty baseline sealing, and crash-safe rollback.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Repo
from git.exc import GitCommandError
from loguru import logger

from ralph.git.diff_entry import DiffEntry
from ralph.git.git_command_queue import route_git_method
from ralph.git.proposal_preflight_error import ProposalPreflightError
from ralph.git.proposal_seal_error import ProposalSealError
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_DIFF_TREE_MIN_FIELDS: int = 5
_NUMSTAT_MIN_FIELDS: int = 3
_NUMSTAT_PATH_INDEX: int = 2
_REF_PARTITION_COUNT: int = 2

#: Upper bound on the blob size whose CONTENT the diff reads for the
#: staged-secret content scan. Same order as the tripwire binary size
#: limit: large enough to hold a credential file, small enough that a
#: multi-megabyte blob never enters the scan. Binary blobs are never
#: read at all.
_MAX_CONTENT_SCAN_BYTES: int = 1_000_000

#: The diff statuses whose recorded path exists in the NEW tree: an
#: addition (``A``), the NEW path of a rename (``R``) and the NEW path
#: of a copy (``C``). A deletion (``D``) records a path that the new
#: tree does NOT hold.
_PROPOSAL_ADDED_STATUSES: frozenset[str] = frozenset({"A", "R", "C"})


@dataclass(frozen=True)
class Seal:
    """A sealed proposal or baseline reference.

    Attributes:
        ref: The git ref name (e.g. ``refs/ralph/proposals/<run>/<iteration>``).
        commit_sha: The 40-character hex SHA of the commit object.
        tree_sha: The 40-character hex SHA of the tree object.
    """

    ref: str
    commit_sha: str
    tree_sha: str


def _open_repo(repo_root: Path | str) -> Repo:
    """Open a GitPython Repo instance for the given repository root."""
    return Repo(Path(repo_root).resolve())


def seal_pending(repo_root: Path | str, run_id: str, iteration: int | str) -> Seal:
    """Seal the current pending state into an isolated proposal ref.

    Creates a Git tree from the current working tree state using a temporary
    index file without mutating the real repository index or working tree.
    Creates a commit object parented on HEAD (if available) and points
    ``refs/ralph/proposals/<run_id>/<iteration>`` to it.

    Args:
        repo_root: Path to the Git repository root.
        run_id: Unique identifier for the pipeline run.
        iteration: Iteration number or identifier.

    Returns:
        Seal instance recording the proposal ref, commit SHA, and tree SHA.

    Raises:
        ProposalSealError: If sealing fails.
    """
    root_path = Path(repo_root).resolve()
    with _open_repo(root_path) as repo:
        ref = f"refs/ralph/proposals/{run_id}/{iteration}"
        parent_sha: str | None = None
        try:
            head_obj = route_git_method(repo, "rev_parse", ("--verify", "HEAD"))
            parent_sha = str(head_obj).strip()
        except GitCommandError:
            parent_sha = None

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_index_path = Path(tmp_dir) / "proposal_index"
            real_index_path = Path(
                repo.index.path
            )  # filesystem-read-ok: inspect real index path to seed temporary index
            if DEFAULT_FILE_BACKEND.exists(real_index_path):
                # filesystem-write-ok: seed the temporary index from the real index to avoid a full rehash
                shutil.copy2(real_index_path, tmp_index_path)

            try:
                with repo.git.custom_environment(GIT_INDEX_FILE=str(tmp_index_path)):
                    route_git_method(repo, "add", ("-A",))
                    route_git_method(
                        repo, "rm", ("-r", "--cached", "--ignore-unmatch", "--", ".agent")
                    )
                    tree_sha_obj = route_git_method(repo, "write_tree")
                    tree_sha = str(tree_sha_obj).strip()
            except Exception as exc:
                raise ProposalSealError(f"Failed to seal tree into temporary index: {exc}") from exc

            commit_msg = f"proposal seal: {run_id}/{iteration}"
            commit_args: list[str] = [tree_sha]
            if parent_sha is not None:
                commit_args.extend(["-p", parent_sha])
            commit_args.extend(["-m", commit_msg])

            try:
                with repo.git.custom_environment(
                    GIT_AUTHOR_NAME="Ralph Workflow",
                    GIT_AUTHOR_EMAIL="noreply@ralphworkflow.com",
                    GIT_COMMITTER_NAME="Ralph Workflow",
                    GIT_COMMITTER_EMAIL="noreply@ralphworkflow.com",
                ):
                    commit_sha_obj = route_git_method(repo, "commit_tree", commit_args)
                commit_sha = str(commit_sha_obj).strip()
                route_git_method(repo, "update_ref", (ref, commit_sha))
            except Exception as exc:
                raise ProposalSealError(f"Failed to create proposal commit/ref: {exc}") from exc

        return Seal(ref=ref, commit_sha=commit_sha, tree_sha=tree_sha)


def seal_baseline(repo_root: Path | str, run_id: str, force: bool = False) -> Seal | None:
    """Record a baseline seal at run start if the working tree is dirty.

    If the repository is dirty (or ``force=True``), captures the current state
    into ``refs/ralph/baselines/<run_id>`` using a temporary index.

    Args:
        repo_root: Path to the Git repository root.
        run_id: Unique identifier for the pipeline run.
        force: If True, creates the baseline seal even if the repository is clean.

    Returns:
        Seal instance if a baseline was created, or None if the tree was clean
        and force was False.

    Raises:
        ProposalSealError: If baseline sealing fails.
    """
    root_path = Path(repo_root).resolve()
    with _open_repo(root_path) as repo:
        if not force:
            status_obj = route_git_method(repo, "status", ("--porcelain", "-uall"))
            if not str(status_obj).strip():
                return None

        ref = f"refs/ralph/baselines/{run_id}"
        parent_sha: str | None = None
        try:
            head_obj = route_git_method(repo, "rev_parse", ("--verify", "HEAD"))
            parent_sha = str(head_obj).strip()
        except GitCommandError:
            parent_sha = None

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_index_path = Path(tmp_dir) / "baseline_index"
            real_index_path = Path(
                repo.index.path
            )  # filesystem-read-ok: inspect real index path to seed temporary index
            if DEFAULT_FILE_BACKEND.exists(real_index_path):
                # filesystem-write-ok: seed the temporary index from the real index to avoid a full rehash
                shutil.copy2(real_index_path, tmp_index_path)

            try:
                with repo.git.custom_environment(GIT_INDEX_FILE=str(tmp_index_path)):
                    route_git_method(repo, "add", ("-A",))
                    route_git_method(
                        repo, "rm", ("-r", "--cached", "--ignore-unmatch", "--", ".agent")
                    )
                    tree_sha_obj = route_git_method(repo, "write_tree")
                    tree_sha = str(tree_sha_obj).strip()
            except Exception as exc:
                raise ProposalSealError(f"Failed to seal baseline tree: {exc}") from exc

            commit_msg = f"baseline seal: {run_id}"
            commit_args: list[str] = [tree_sha]
            if parent_sha is not None:
                commit_args.extend(["-p", parent_sha])
            commit_args.extend(["-m", commit_msg])

            try:
                commit_sha_obj = route_git_method(repo, "commit_tree", commit_args)
                commit_sha = str(commit_sha_obj).strip()
                route_git_method(repo, "update_ref", (ref, commit_sha))
            except Exception as exc:
                raise ProposalSealError(f"Failed to create baseline commit/ref: {exc}") from exc

        return Seal(ref=ref, commit_sha=commit_sha, tree_sha=tree_sha)


def _collect_binary_paths(repo: Repo, base: str, ref: str) -> set[str]:
    """Identify paths classified as binary by Git between base and ref."""
    binary_paths: set[str] = set()
    try:
        raw_numstat_obj = route_git_method(
            repo,
            "diff_tree",
            ("-r", "-z", "--numstat", "--no-commit-id", base, ref),
        )
        raw_numstat = str(raw_numstat_obj)
        if raw_numstat:
            for record in raw_numstat.split("\0"):
                if not record.strip():
                    continue
                fields = record.split("\t")
                if len(fields) >= _NUMSTAT_MIN_FIELDS and fields[0] == "-" and fields[1] == "-":
                    binary_paths.add(fields[_NUMSTAT_PATH_INDEX])
    except Exception:
        pass
    return binary_paths


def _collect_base_tree_paths(repo: Repo, base: str) -> frozenset[str] | None:
    """Return the tracked paths present in the tree the diff is computed against.

    A recursive ``git diff-tree`` reports BLOB entries only, so the removal of
    a whole directory arrives as one deleted blob path per tracked file and
    never as a directory entry. The membership of the base tree is therefore
    the only evidence that separates "the directory is gone in its entirety"
    from "some files under it are gone".

    ``--full-tree`` is required: without it ``git ls-tree`` limits the listing
    to the current working directory and reports paths relative to it.

    Returns:
        The tracked paths of the base tree, an EMPTY set when the base tree
        holds no tracked path, or ``None`` when the membership could not be
        read. ``None`` is not an empty tree: it means the denominator of the
        mass-deletion fraction is unknown, so the caller must fail closed
        instead of measuring the fraction against a denominator it cannot
        trust.
    """
    try:
        raw_obj = route_git_method(
            repo,
            "ls_tree",
            ("-r", "--name-only", "--full-tree", "-z", base),
        )
    except Exception:
        return None
    raw = str(raw_obj)
    return frozenset(path for path in raw.split("\0") if path)


def _read_bounded_blob_content(
    repo: Repo,
    blob_sha: str,
    size: int,
    is_binary: bool,
) -> str | None:
    """Return the text content of a blob, bounded and only for text blobs.

    The staged-secret CONTENT scan needs the blob text; reading every blob
    would be unbounded work, so only NON-BINARY blobs up to
    ``_MAX_CONTENT_SCAN_BYTES`` are read. A blob that cannot be read yields
    ``None`` and the content scan stays silent for it.
    """
    if not blob_sha or is_binary or size <= 0 or size > _MAX_CONTENT_SCAN_BYTES:
        return None
    try:
        raw_obj = route_git_method(repo, "cat_file", ("blob", blob_sha))
    except Exception:
        return None
    return str(raw_obj)


def diff_tree(repo_root: Path | str, ref: str, base: str = "HEAD") -> list[DiffEntry]:
    """Compute structural diff entries between base and ref.

    Args:
        repo_root: Path to the Git repository root.
        ref: The proposal ref or commit SHA to compare.
        base: The base ref or commit SHA (defaults to 'HEAD').

    Returns:
        List of DiffEntry records describing changed paths, modes, sizes, and binary status.
        Every entry also carries the tracked paths of the base tree, or ``None``
        when that membership could not be read.

    Raises:
        ProposalSealError: If diff-tree execution fails.
    """
    root_path = Path(repo_root).resolve()
    with _open_repo(root_path) as repo:
        try:
            raw_obj = route_git_method(
                repo,
                "diff_tree",
                ("-r", "-z", "--no-commit-id", base, ref),
            )
        except Exception as exc:
            raise ProposalSealError(
                f"Failed to run diff-tree between {base} and {ref}: {exc}"
            ) from exc

        raw = str(raw_obj)
        if not raw:
            return []

        binary_paths = _collect_binary_paths(repo, base, ref)
        base_tree_paths = _collect_base_tree_paths(repo, base)

        tokens = raw.split("\0")
        entries: list[DiffEntry] = []
        i = 0
        while i < len(tokens):
            meta = tokens[i].strip()
            if not meta:
                i += 1
                continue
            parts = meta.split()
            if len(parts) < _DIFF_TREE_MIN_FIELDS:
                i += 1
                continue
            old_mode = parts[0].lstrip(":")
            new_mode = parts[1]
            new_sha = parts[3]
            status_token = parts[4]
            status = status_token[0]

            if i + 1 >= len(tokens):
                break
            path = tokens[i + 1]
            if status in ("R", "C") and i + 2 < len(tokens):
                path = tokens[i + 2]
                i += 3
            else:
                i += 2

            size = 0
            is_binary = path in binary_paths
            if status != "D":
                try:
                    size_obj = route_git_method(repo, "cat_file", ("-s", new_sha))
                    size_str = str(size_obj).strip()
                    size = int(size_str)
                except Exception:
                    size = 0

            entries.append(
                DiffEntry(
                    path=path,
                    status=status,
                    old_mode=old_mode,
                    new_mode=new_mode,
                    size=size,
                    is_binary=is_binary,
                    content=_read_bounded_blob_content(repo, new_sha, size, is_binary),
                    base_tree_paths=base_tree_paths,
                )
            )

        return entries


def preflight(repo_root: Path | str, ref: str) -> None:
    """Preflight check that HEAD and proposal ref exist and resolve.

    Args:
        repo_root: Path to the Git repository root.
        ref: Proposal ref to verify.

    Raises:
        ProposalPreflightError: If repo_root is invalid, .git is damaged or missing,
            HEAD does not resolve, or ref does not resolve.
    """
    root_path = Path(repo_root).resolve()
    git_dir = (
        root_path / ".git"
    )  # filesystem-read-ok: verify .git directory exists before git operations
    if not git_dir.exists():  # filesystem-read-ok: verify .git presence
        raise ProposalPreflightError(f"Repository metadata missing or damaged at {git_dir}")

    try:
        with _open_repo(root_path) as repo:
            try:
                route_git_method(repo, "rev_parse", ("--verify", "HEAD"))
            except (GitCommandError, OSError) as exc:
                raise ProposalPreflightError(f"HEAD does not resolve: {exc}") from exc

            try:
                route_git_method(repo, "rev_parse", ("--verify", ref))
            except (GitCommandError, OSError) as exc:
                raise ProposalPreflightError(
                    f"Proposal ref {ref!r} does not resolve: {exc}"
                ) from exc
    except ProposalPreflightError:
        raise
    except Exception as exc:
        raise ProposalPreflightError(f"Repository access failed for {repo_root}: {exc}") from exc


def _proposal_ref_sort_key(ref_name: str) -> tuple[int, str]:
    """Sort key placing higher numeric iterations first."""
    parts = ref_name.rsplit("/", 1)
    if len(parts) == _REF_PARTITION_COUNT and parts[1].isdigit():
        return (int(parts[1]), ref_name)
    return (-1, ref_name)


def prune(
    repo_root: Path | str,
    run_id: str | None = None,
    keep_refs: Iterable[str] | None = None,
    max_retained: int | None = None,
) -> list[str]:
    """Prune proposal refs according to retention rules.

    Args:
        repo_root: Path to the Git repository root.
        run_id: Optional run_id filter. If provided, only refs under
            ``refs/ralph/proposals/<run_id>/`` are considered.
        keep_refs: Optional collection of ref names that must not be pruned.
        max_retained: Optional maximum number of recent proposal refs to retain.

    Returns:
        List of ref names that were pruned.
    """
    root_path = Path(repo_root).resolve()
    with _open_repo(root_path) as repo:
        prefix = f"refs/ralph/proposals/{run_id}" if run_id is not None else "refs/ralph/proposals"
        raw_refs_obj = route_git_method(
            repo,
            "for_each_ref",
            (prefix, "--format=%(refname)"),
        )
        raw_refs = str(raw_refs_obj)
        matching_refs = [line.strip() for line in raw_refs.splitlines() if line.strip()]
        matching_refs.sort(key=_proposal_ref_sort_key, reverse=True)

        keep_set = set(keep_refs) if keep_refs is not None else set()

        pruned: list[str] = []
        retained_count = 0
        for ref_name in matching_refs:
            if ref_name in keep_set:
                continue
            if max_retained is not None and retained_count < max_retained:
                retained_count += 1
                continue
            route_git_method(repo, "update_ref", ("-d", ref_name))
            pruned.append(ref_name)

        return pruned


def _remove_proposal_added_files(root_path: Path, diff_entries: list[DiffEntry]) -> None:
    """Remove files added in the proposal relative to the target ref."""
    for entry in diff_entries:
        if entry.status not in _PROPOSAL_ADDED_STATUSES:
            continue
        rel_path = entry.path
        if rel_path.startswith(".agent/") or rel_path == ".agent":
            continue
        if rel_path.startswith(".git/") or rel_path == ".git":
            continue

        target_file = root_path / rel_path
        # filesystem-read-ok: check file or symlink existence before removing added proposal file
        if target_file.is_file() or target_file.is_symlink():
            target_file.unlink()  # filesystem-write-ok: rollback removes files added in proposal relative to target
            parent = target_file.parent
            while parent != root_path:
                if parent == root_path / ".agent" or parent == root_path / ".git":
                    break
                try:
                    parent.rmdir()  # filesystem-write-ok: rollback cleans empty directory left by removed proposal file
                    parent = parent.parent
                except OSError:
                    break


def rollback(
    repo_root: Path | str,
    ref: str,
    baseline_ref: str | None = None,
    step_hook: Callable[[str], None] | None = None,
) -> None:
    """Roll back the repository working tree and index to HEAD or baseline.

    Idempotent and crash-safe restore. Restores tracked files via
    ``git read-tree --reset -u <target>`` and removes files that were added
    in the proposal tree and absent from the target, preserving ignored files
    and ``.agent/`` runtime state.

    Args:
        repo_root: Path to the Git repository root.
        ref: The proposal ref to roll back.
        baseline_ref: Optional baseline ref to restore to instead of HEAD.
        step_hook: Optional hook called at each step for crash-safety testing.

    Raises:
        ProposalPreflightError: If preflight checks fail.
        ProposalSealError: If rollback fails.
    """
    if step_hook is not None:
        step_hook("preflight")

    preflight(repo_root, ref)

    root_path = Path(repo_root).resolve()
    with _open_repo(root_path) as repo:
        target_ref = "HEAD"
        if baseline_ref is not None:
            try:
                route_git_method(repo, "rev_parse", ("--verify", baseline_ref))
                target_ref = baseline_ref
            except (GitCommandError, OSError) as exc:
                raise ProposalPreflightError(
                    f"Baseline ref {baseline_ref!r} does not resolve: {exc}"
                ) from exc

        if step_hook is not None:
            step_hook("read_tree")

        try:
            route_git_method(repo, "read_tree", ("--reset", "-u", target_ref))
        except Exception as exc:
            raise ProposalSealError(f"Failed to reset tree to {target_ref}: {exc}") from exc

        if step_hook is not None:
            step_hook("remove_added_files")

        diff_entries = diff_tree(repo_root, ref=ref, base=target_ref)
        _remove_proposal_added_files(root_path, diff_entries)

        if step_hook is not None:
            step_hook("complete")

        _mark_rejected_proposal(root_path, ref)


#: Namespace a rolled-back proposal is marked under, mirroring the proposal
#: namespace segment for segment.
REJECTED_PROPOSAL_REF_PREFIX = "refs/ralph/rejected-proposals/"
_PROPOSAL_REF_PREFIX = "refs/ralph/proposals/"


def _mark_rejected_proposal(repo_root: Path, proposal_ref: str) -> None:
    """Record a rolled-back proposal ref so the sweep keeps it for the run.

    A rolled-back proposal is a REJECTED proposal: its tree never became a
    commit, and ``git checkout <ref> -- .`` is the documented recovery. The
    mark lives in its own ref namespace, so the ref is protected by NAME and
    never by the tree it happens to carry: a later iteration that reproduces
    the same tree must not make the rejected ref prunable.
    """
    if not proposal_ref.startswith(_PROPOSAL_REF_PREFIX):
        return
    marker_ref = REJECTED_PROPOSAL_REF_PREFIX + proposal_ref[len(_PROPOSAL_REF_PREFIX) :]
    try:
        with _open_repo(repo_root) as repo:
            sha = cast("str", route_git_method(repo, "rev_parse", ("--verify", proposal_ref)))
            route_git_method(repo, "update_ref", (marker_ref, sha))
    except Exception as exc:
        # The mark only protects a ref; a failure here must not fail the
        # rollback that already restored the tree.
        logger.warning(
            "Could not mark the rejected proposal ref {ref}: {err}",
            ref=proposal_ref,
            err=exc,
        )


__all__ = [
    "DiffEntry",
    "ProposalPreflightError",
    "ProposalSealError",
    "REJECTED_PROPOSAL_REF_PREFIX",
    "Seal",
    "diff_tree",
    "preflight",
    "prune",
    "rollback",
    "seal_baseline",
    "seal_pending",
]
