"""AC-11: hostile user git config must not break integration.

AC-11 says: "with hostile user config (rerere.enabled+autoUpdate
with a wrong recorded resolution, commit.gpgsign with no key,
rebase.autoSquash, rebase.updateRefs, autostash, interactive
sequence editor) integration still lands unchanged -- each knob
proven neutralized."

The production side already neutralizes the hostile knobs through
:data:`ralph.git.hardening.PINNED_CONFIG_ARGS` (``-c rerere.enabled=false``,
``-c commit.gpgsign=false``, ``-c tag.gpgsign=false``, ``-c
core.fsmonitor=false``) and the explicit ``--no-autostash`` /
``--no-autosquash`` / ``--no-update-refs`` flags on the rebase argv.
This test is the audit that proves the neutralization actually
happens -- that integration lands WHEN the user config is hostile,
NOT that integration only lands on a clean config.

The test sets the hostile config via ``git config`` in the per-test
repo (NOT ``--global``, so a CI runner's host git config does not
leak between tests) and asserts the integration outcome plus the
specific neutralization signal. A ``finally`` block resets every
config key the test wrote, even if an assertion fails, so the next
test sees a clean repo regardless of which assertion tripped.

Same conventions as
:mod:`tests.test_auto_integrate_rebase_conflict_e2e` (real per-test
git repositories; helpers duplicated to avoid brittle cross-module
imports).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


def _run(
    repo_root: Path, *args: str, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def _make_feature_branch_with_conflict(tmp_git_repo: Path) -> str:
    """One feature commit + one base commit that conflict on shared.txt."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature edit")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version\n", "base edit")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def test_rerere_with_wrong_recorded_resolution_does_not_replay_silently(
    tmp_path_factory: pytest.TempPathFactory, _git_repo_template: Path
) -> None:
    """A pre-recorded wrong rerere resolution must not be auto-replayed.

    The hostile config: ``rerere.enabled=true`` + ``rerere.autoUpdate=true`` +
    a wrong resolution pre-populated in the rr-cache. Without the
    ``-c rerere.enabled=false`` pin from :data:`PINNED_CONFIG_ARGS`, git
    would silently replay the wrong recorded resolution on the next
    merge/rebase that hit the same conflict, producing a commit with the
    wrong content and zero conflict signal.

    With the pin, rerere is OFF for the integration run; the conflict
    is left to the resolver (a stub that picks the feature's version)
    and the wrong rr-cache entry is never consulted. The test
    asserts THREE things:

    1. Integration lands: ``last_action`` in ``{"rebased", "merged"}``
       and ``fast_forwarded is True``. AC-11 requires landing, not
       mere progression; a ``conflict`` outcome means the integration
       could not proceed.
    2. The landed content is the resolver's pick, NOT the rr-cache's
       wrong sentinel. If rerere were active, the wrong postimage
       (``RERERE-WRONG-RESOLUTION-MARKER``) would have been
       committed to the conflicting file.
    3. The rr-cache entries we stamped survive untouched. A
       consumed entry would either be deleted or have its bytes
       changed; an untouched entry is direct proof that rerere
       was OFF.
    """
    root = tmp_path_factory.mktemp("rerere-hostile") / "repo"
    shutil.copytree(_git_repo_template, root)
    base = _make_feature_branch_with_conflict(root)

    _run(root, "config", "rerere.enabled", "true")
    _run(root, "config", "rerere.autoupdate", "true")

    # Trigger a recorded conflict (without using rebase) so
    # rerere writes rr-cache entries; then stamp every
    # postimage with a sentinel so a replay attempt would
    # write the sentinel into the conflicting file.
    _run(root, "merge", "feature", "--no-commit", "--no-ff")
    _run(root, "merge", "--abort")
    rr_dir = root / ".git" / "rr-cache"
    if rr_dir.exists():
        for conflict_dir in rr_dir.iterdir():
            for variant_dir in conflict_dir.iterdir():
                if variant_dir.is_dir():
                    postimage = variant_dir / "postimage"
                    if postimage.exists():
                        postimage.write_text(
                            "RERERE-WRONG-RESOLUTION-MARKER\n", encoding="utf-8"
                        )

    rr_dir = root / ".git" / "rr-cache"
    pre_fingerprints: dict[Path, bytes] = {}
    if rr_dir.exists():
        for postimage in rr_dir.rglob("postimage"):
            pre_fingerprints[postimage] = postimage.read_bytes()

    # A resolver that picks the feature's version of the
    # conflicting file -- deterministically, so the test
    # can assert the landed content is the feature's
    # version, NOT the wrong rr-cache marker. The resolver
    # is invoked only because rerere is OFF (the
    # ``-c rerere.enabled=false`` pin neutralized the
    # hostile config); the test would be tautological
    # otherwise.
    def _pick_feature(_root: Path, _paths: list[str]) -> bool:
        shared = root / "shared.txt"
        shared.write_text("feature version\n", encoding="utf-8")
        _run(root, "add", "shared.txt")
        return True

    try:
        outcome = auto_integrate_after_commit(
            _build_config(base),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=_pick_feature,
        )
    finally:
        _run(root, "config", "--unset", "rerere.enabled")
        _run(root, "config", "--unset", "rerere.autoupdate")

    assert outcome is not None
    # AC-11 requires integration to LAND despite the hostile
    # rerere config, not merely to advance through some
    # recorded state. A `conflict` outcome means the endpoint
    # merge did not land and the target was not advanced, which
    # is the failure shape AC-11 forbids. The resolver handles
    # the content conflict; the pinned rerere.disabled is what
    # prevents the wrong rr-cache entry from being silently
    # replayed.
    assert outcome.last_action in {"rebased", "merged"}, (
        f"AC-11 hostile-rerere integration did NOT land; "
        f"last_action={outcome.last_action!r}, last_reason={outcome.last_reason!r}. "
        f"A `conflict` outcome is the failure shape AC-11 forbids."
    )
    assert outcome.fast_forwarded is True, (
        f"AC-11 hostile-rerere integration did not fast-forward the target; "
        f"outcome={outcome!r}. AC-11 requires integration to land unchanged "
        f"even with hostile config."
    )

    # AC-11 neutralization proof 1: the landed content is
    # the resolver's pick (the feature's version), NOT the
    # rr-cache's wrong sentinel. A replayed wrong
    # resolution would have written
    # ``RERERE-WRONG-RESOLUTION-MARKER`` into the
    # conflicting file.
    landed = (root / "shared.txt").read_text(encoding="utf-8")
    assert "RERERE-WRONG-RESOLUTION-MARKER" not in landed, (
        f"AC-11 hostile-rerere integration replayed the wrong rr-cache "
        f"resolution; the conflicting file carries the wrong sentinel: "
        f"{landed!r}. The rerere.enabled=false pin was bypassed."
    )
    assert landed == "feature version\n", (
        f"AC-11 hostile-rerere integration did not land the resolver's "
        f"pick; got {landed!r}, expected 'feature version\\n'. Either "
        "the resolver did not run or the integration did not commit."
    )
    # The target ref MUST equal the rebased feature tip on a
    # successful fast-forward. The exact equality is the
    # landing proof AC-11 requires.
    target_sha = _run(root, "rev-parse", f"refs/heads/{base}").stdout.strip()
    feature_sha = _run(root, "rev-parse", "feature").stdout.strip()
    assert target_sha == feature_sha, (
        f"AC-11 hostile-rerere integration did not fast-forward the "
        f"target to the feature tip; target={target_sha!r}, "
        f"feature={feature_sha!r}"
    )

    # AC-11 neutralization proof 2: the rr-cache entries we
    # stamped must NOT have been consumed by the integration.
    # A consumed entry would either be deleted or have its
    # bytes changed; an untouched entry is direct proof that
    # rerere was OFF.
    for path, expected_bytes in pre_fingerprints.items():
        if not path.exists():
            pytest.fail(
                f"rr-cache postimage at {path} was deleted during the "
                "integration run; rerere was active. The AC-11 "
                "neutralization failed."
            )
        actual = path.read_bytes()
        assert actual == expected_bytes, (
            f"rr-cache postimage at {path} was modified during the "
            "integration run; rerere was active. The AC-11 "
            "neutralization failed."
        )


def test_commit_gpgsign_with_no_key_does_not_hang_or_fail_replay(
    tmp_path_factory: pytest.TempPathFactory, _git_repo_template: Path
) -> None:
    """commit.gpgsign with a missing signing key must not fail or hang.

    Without the ``-c commit.gpgsign=false`` pin, git would try to
    invoke gpg on every replayed commit and either hang on a
    pinentry prompt or fail with "No secret key". With the pin,
    gpgsign is OFF for the integration and replayed commits land
    normally.

    Setup: feature adds a feature-only file; the base advances
    on a base-only file. The integration is a real
    conflict-free rebase so the ``commit.gpgsign`` knob
    would fire on every replayed commit (the base commit
    itself + the feature commit, both replayed onto the
    new base). A conflict would let the endpoint-merge
    fallback mask whether the pin worked, so the
    topology is intentionally disjoint.
    """
    root = tmp_path_factory.mktemp("gpgsign-hostile") / "repo"
    shutil.copytree(_git_repo_template, root)
    base = _base_branch(root)
    _run(root, "checkout", "-b", "feature")
    _commit(root, "feature.txt", "feature content\n", "feature edit")
    _run(root, "checkout", base)
    _commit(root, "base.txt", "base content\n", "base edit")
    _run(root, "checkout", "feature")

    _run(root, "config", "commit.gpgsign", "true")
    _run(root, "config", "user.signingkey", "0123456789ABCDEF-nonexistent")

    try:
        outcome = auto_integrate_after_commit(
            _build_config(base),
            WorkspaceScope(root),
            RebaseState(),
        )
        # Capture the hostile-config fingerprint BEFORE the
        # ``finally`` block clears it -- the assertion that
        # proves the integration succeeded DESPITE the
        # hostile config needs to read the config state mid-
        # integration, not post-cleanup.
        post = _run(root, "config", "--get", "commit.gpgsign").stdout.strip()
    finally:
        _run(root, "config", "--unset", "commit.gpgsign")
        _run(root, "config", "--unset", "user.signingkey")

    assert outcome is not None
    # AC-11: integration lands despite hostile commit.gpgsign
    # config. The pin ``-c commit.gpgsign=false`` neutralizes
    # the missing key; a `conflict` outcome would mean the
    # integration could not proceed, which is the failure
    # shape AC-11 forbids. The test's conflict-free
    # topology proves the pin is what kept the integration
    # going -- without it, every replayed commit would
    # have tried to invoke gpg and either hung on a
    # pinentry or failed with "No secret key".
    assert outcome.last_action in {"rebased", "merged"}, (
        f"AC-11 hostile-gpgsign integration did NOT land; "
        f"last_action={outcome.last_action!r}, last_reason={outcome.last_reason!r}. "
        f"A `conflict` outcome is the failure shape AC-11 forbids."
    )
    assert outcome.fast_forwarded is True, (
        f"AC-11 hostile-gpgsign integration did not fast-forward the target; "
        f"outcome={outcome!r}. AC-11 requires integration to land unchanged "
        f"even with hostile config."
    )
    # The target ref MUST equal the rebased feature tip on a
    # successful fast-forward. The exact equality is the
    # landing proof AC-11 requires.
    target_sha = _run(root, "rev-parse", f"refs/heads/{base}").stdout.strip()
    feature_sha = _run(root, "rev-parse", "feature").stdout.strip()
    assert target_sha == feature_sha, (
        f"AC-11 hostile-gpgsign integration did not fast-forward the "
        f"target to the feature tip; target={target_sha!r}, "
        f"feature={feature_sha!r}"
    )

    # Sanity check: the hostile config was still set during the
    # integration (proving the integration succeeded despite
    # the config, not because a cleanup side-effect disabled it).
    assert post == "true", (
        f"commit.gpgsign was unexpectedly cleared DURING the integration; "
        f"got {post!r}. The AC-11 neutralization proof requires the "
        "hostile config to STILL be set when the integration completes."
    )


def test_rebase_autosquash_does_not_open_editor(
    tmp_path_factory: pytest.TempPathFactory, _git_repo_template: Path
) -> None:
    """rebase.autoSquash=true with a fixup! commit must not open an editor.

    Without the explicit ``--no-autosquash`` flag, git would open
    the sequence editor and either hang waiting for the operator
    or silently pick the wrong auto-squashed commit. With the
    pin, the fixup! commit lands as an ordinary commit, history
    is preserved verbatim, and the integration completes without
    touching the editor.
    """
    root = tmp_path_factory.mktemp("autosquash-hostile") / "repo"
    shutil.copytree(_git_repo_template, root)
    base = _base_branch(root)
    _run(root, "checkout", "-b", "feature")
    _commit(root, "feature.txt", "feature one\n", "feature edit one")
    _commit(root, "feature.txt", "feature two\n", "fixup! feature edit one")
    # Advance the base so the integration has a real rebase to
    # perform -- without a base-side commit the integration
    # short-circuits with ``on target branch`` and never starts.
    _run(root, "checkout", base)
    _commit(root, "base.txt", "base addition\n", "base edit")
    _run(root, "checkout", "feature")

    _run(root, "config", "rebase.autoSquash", "true")

    try:
        outcome = auto_integrate_after_commit(
            _build_config(base),
            WorkspaceScope(root),
            RebaseState(),
        )
    finally:
        _run(root, "config", "--unset", "rebase.autoSquash")

    assert outcome is not None
    assert outcome.last_action == "rebased", outcome.last_action

    # The fixup! commit survived as a separate commit, NOT
    # squashed into its target. After a successful rebase+ff
    # HEAD is on the base (now advanced past the rebased
    # feature tip); the rebased feature commits are visible
    # via the ``feature`` ref or the log of HEAD.
    feature_commits = _run(
        root, "log", "HEAD", "--format=%s"
    ).stdout.splitlines()
    assert "fixup! feature edit one" in feature_commits, (
        f"fixup! commit was auto-squashed despite --no-autosquash pin; "
        f"feature commits: {feature_commits!r}"
    )
    assert "feature edit one" in feature_commits, (
        f"original feature commit was lost during the rebase; "
        f"feature commits: {feature_commits!r}"
    )


def test_rebase_update_refs_does_not_move_other_branches(
    tmp_path_factory: pytest.TempPathFactory, _git_repo_template: Path
) -> None:
    """rebase.updateRefs=true must not force-move other branches or labels.

    Without the explicit ``--no-update-refs`` flag, git would
    force-move every ref that points into the replay range --
    including branches checked out in sibling worktrees and local
    labels the operator created. With the pin, only ``feature``
    moves.
    """
    root = tmp_path_factory.mktemp("update-refs-hostile") / "repo"
    shutil.copytree(_git_repo_template, root)
    base = _base_branch(root)
    _run(root, "checkout", "-b", "feature")
    _commit(root, "feature.txt", "feature\n", "feature commit")
    feature_sha = _run(root, "rev-parse", "HEAD").stdout.strip()

    # Two branches point at the same feature commit: ``feature``
    # is the one we will rebase, ``sibling`` is the one we want
    # to prove stays untouched.
    _run(root, "branch", "sibling", feature_sha)
    _run(root, "tag", "feature-label", feature_sha)

    sibling_sha_before = _run(root, "rev-parse", "sibling").stdout.strip()
    label_sha_before = _run(root, "rev-parse", "feature-label").stdout.strip()

    # Advance the base so the integration has a real rebase to
    # perform -- without a base-side commit the integration
    # short-circuits with ``on target branch`` and never starts.
    _run(root, "checkout", base)
    _commit(root, "base.txt", "base addition\n", "base edit")
    _run(root, "checkout", "feature")

    _run(root, "config", "rebase.updateRefs", "true")

    try:
        outcome = auto_integrate_after_commit(
            _build_config(base),
            WorkspaceScope(root),
            RebaseState(),
        )
    finally:
        _run(root, "config", "--unset", "rebase.updateRefs")

    assert outcome is not None
    assert outcome.last_action == "rebased", outcome.last_action

    sibling_sha_after = _run(root, "rev-parse", "sibling").stdout.strip()
    label_sha_after = _run(root, "rev-parse", "feature-label").stdout.strip()
    assert sibling_sha_after == sibling_sha_before, (
        f"sibling branch was force-moved by rebase.updateRefs; "
        f"before={sibling_sha_before!r} after={sibling_sha_after!r}"
    )
    assert label_sha_after == label_sha_before, (
        f"feature-label tag was force-moved by rebase.updateRefs; "
        f"before={label_sha_before!r} after={label_sha_after!r}"
    )


def test_autostash_does_not_create_a_stranded_stash(
    tmp_path_factory: pytest.TempPathFactory, _git_repo_template: Path
) -> None:
    """A dirty worktree must not start integration or create an autostash.

    Without the explicit ``--no-autostash`` flag, git would
    autostash the operator's tracked modifications and potentially
    strand them in a stash entry. With the pin AND the clean-tree
    precondition, the integration refuses to start and no stash
    entry is created.
    """
    root = tmp_path_factory.mktemp("autostash-hostile") / "repo"
    shutil.copytree(_git_repo_template, root)
    base = _base_branch(root)
    # Branch off the base, commit on the feature branch, then
    # advance the base so the integration has work to do, then
    # come back to the base and dirty the worktree so the
    # clean-tree precondition refuses to start.
    _run(root, "checkout", "-b", "feature")
    _commit(root, "feature.txt", "feature one\n", "feature edit")
    feature_sha_before = _run(root, "rev-parse", "feature").stdout.strip()
    _run(root, "checkout", base)
    _commit(root, "base.txt", "base addition\n", "base edit")

    # Dirty the worktree on the base's working tree. We modify
    # the existing README.md (which exists on the base) so the
    # precondition sees a tracked modification.
    tracked = root / "README.md"
    tracked.write_text("operator-dirty-edit\n", encoding="utf-8")

    # Verify the dirty state is detected by the same surface the
    # precondition uses (``git status --porcelain
    # --untracked-files=no``).
    pre_status = _run(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    assert pre_status, (
        "test setup did not produce a dirty tracked file the "
        "precondition would see; cannot validate the "
        "AC-11 autostash neutralization"
    )

    _run(root, "config", "rebase.autoStash", "true")

    try:
        outcome = auto_integrate_after_commit(
            _build_config(base),
            WorkspaceScope(root),
            RebaseState(),
        )
    finally:
        _run(root, "config", "--unset", "rebase.autoStash")

    assert outcome is not None
    feature_sha_after = _run(root, "rev-parse", "feature").stdout.strip()

    # The integration recorded a skip (dirty tree); the feature
    # ref was never rebased.
    assert feature_sha_after == feature_sha_before, (
        f"feature ref was moved despite dirty-tree precondition: "
        f"before={feature_sha_before!r} after={feature_sha_after!r}; "
        f"outcome.last_action={outcome.last_action!r} "
        f"outcome.last_reason={outcome.last_reason!r}"
    )

    # AC-11 neutralization proof: no stash entry exists.
    stash_list = _run(root, "stash", "list").stdout.strip()
    assert stash_list == "", (
        f"autostash created a stranded stash entry despite "
        f"--no-autostash pin: {stash_list!r}"
    )

    # The operator's dirty edit is still on disk (the precondition
    # refused to start, so the worktree was not touched).
    assert "operator-dirty-edit" in tracked.read_text(encoding="utf-8")
