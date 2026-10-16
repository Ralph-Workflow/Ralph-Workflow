"""U5: worktree-qualified backup-ref identity and scoped deletion.

Real-Git cases prove that two linked worktrees starting from the same
pre-feature SHA get DISTINCT backup refs, that completing the operation
in one worktree preserves the other's recovery ref, and that legacy
SHA-only refs survive cleanup (fail-closed under-delete). Unit-shaped
cases pin the qualified-name helper and the exact-name-only deletion
contract without spawning git.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.auto_integrate_backup_refs import (
    create_rebase_backup_ref,
    delete_rebase_backup_ref,
    qualified_backup_ref,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_recovery_terminal import (
    delete_rebase_backup_refs as _term_del,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


def _run(repo_root: Path, *args: str):
    return run_git(
        args,
        cwd=repo_root,
        label="backup-ref-scope-test",
        options=GitRunOptions(timeout=15),
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _backup_refs(repo_root: Path) -> list[str]:
    out = _run(repo_root, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/")
    assert out.returncode == 0
    return sorted(line.strip() for line in out.stdout.splitlines() if line.strip())


def _two_worktrees(tmp_path: Path) -> tuple[Path, Path, str]:
    """Shared repo with two linked worktrees on the same pre-feature SHA."""
    root = tmp_path / "repo"
    root.mkdir()
    assert _run(root.parent, "init", "-b", "main", str(root)).returncode == 0
    _run(root, "config", "user.email", "t@example.com")
    _run(root, "config", "user.name", "t")
    _commit(root, "seed.txt", "seed\n", "seed")
    pre_feature_sha = _commit(root, "feature.txt", "f\n", "feature base")
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    assert _run(root, "worktree", "add", "-b", "feature-a", str(wt_a)).returncode == 0
    assert _run(root, "worktree", "add", "-b", "feature-b", str(wt_b)).returncode == 0
    return wt_a, wt_b, pre_feature_sha


def test_qualified_backup_ref_is_deterministic_and_worktree_scoped() -> None:
    ref_none = qualified_backup_ref(None, "a" * 40)
    ref_a = qualified_backup_ref("/wt/a", "a" * 40)
    ref_b = qualified_backup_ref("/wt/b", "a" * 40)
    assert ref_none == qualified_backup_ref(None, "a" * 40)
    assert ref_a != ref_b
    assert ref_a.startswith("refs/rebase-backup/")
    assert ref_a.endswith("-" + "a" * 40)
    # wt8 = sha256(str(owning_worktree))[:8]
    import hashlib

    assert ref_a.split("/")[2].startswith(hashlib.sha256(b"/wt/a").hexdigest()[:8])


def test_two_linked_worktrees_get_distinct_backup_refs(tmp_path: Path) -> None:
    wt_a, wt_b, pre_sha = _two_worktrees(tmp_path)
    ref_a = create_rebase_backup_ref(wt_a, pre_sha, str(wt_a))
    ref_b = create_rebase_backup_ref(wt_b, pre_sha, str(wt_b))
    assert ref_a is not None and ref_b is not None
    assert ref_a != ref_b, "same pre-feature SHA must not share one ref across worktrees"
    assert set(_backup_refs(wt_a)) == {ref_a, ref_b}


def test_completing_worktree_a_preserves_worktree_b_ref(tmp_path: Path) -> None:
    wt_a, wt_b, pre_sha = _two_worktrees(tmp_path)
    ref_a = create_rebase_backup_ref(wt_a, pre_sha, str(wt_a))
    ref_b = create_rebase_backup_ref(wt_b, pre_sha, str(wt_b))
    assert ref_a and ref_b
    record_b = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=pre_sha,
        pre_target_sha=pre_sha,
        owning_worktree=str(wt_b),
    )
    # Worktree A's operation completes; deletion is by ITS exact name only.
    _term_del(wt_a, ref_a)
    refs = _backup_refs(wt_a)
    assert ref_a not in refs
    assert ref_b in refs, "worktree B's only recovery ref must survive A's cleanup"
    # Symmetrically, B's record-scoped deletion removes only B's ref.
    _term_del(wt_a, qualified_backup_ref(record_b.owning_worktree, record_b.pre_feature_sha))
    assert _backup_refs(wt_a) == []


def test_legacy_sha_only_ref_survives_cleanup(tmp_path: Path) -> None:
    wt_a, _wt_b, pre_sha = _two_worktrees(tmp_path)
    legacy_ref = f"refs/rebase-backup/{pre_sha[:8]}-{pre_sha}"
    assert _run(wt_a, "update-ref", legacy_ref, pre_sha).returncode == 0
    ref_a = create_rebase_backup_ref(wt_a, pre_sha, str(wt_a))
    assert ref_a
    _term_del(wt_a, ref_a)
    refs = _backup_refs(wt_a)
    assert legacy_ref in refs, "legacy SHA-only ref is never deleted (fail-closed under-delete)"
    # Exact-name deletion of a legacy ref is also refused.
    _term_del(wt_a, legacy_ref)
    assert legacy_ref in _backup_refs(wt_a)
    # None ref: warn-and-return, namespace untouched.
    _term_del(wt_a, None)
    assert legacy_ref in _backup_refs(wt_a)


def test_create_with_none_owning_worktree_falls_back_to_root(tmp_path: Path) -> None:
    root = tmp_path / "solo"
    root.mkdir()
    assert _run(root.parent, "init", "-b", "main", str(root)).returncode == 0
    _run(root, "config", "user.email", "t@example.com")
    _run(root, "config", "user.name", "t")
    sha = _commit(root, "f.txt", "f\n", "one")
    ref = create_rebase_backup_ref(root, sha, None)
    assert ref == qualified_backup_ref(str(root), sha)
    assert _backup_refs(root) == [ref]


def test_delete_rebase_backup_ref_exact_name_unchanged(tmp_path: Path) -> None:
    wt_a, _wt_b, pre_sha = _two_worktrees(tmp_path)
    ref_a = create_rebase_backup_ref(wt_a, pre_sha, str(wt_a))
    assert ref_a
    delete_rebase_backup_ref(wt_a, ref_a)
    assert _backup_refs(wt_a) == []
