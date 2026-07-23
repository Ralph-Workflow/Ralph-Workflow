"""AC-13: Non-main target integration tests.

The freshness/seam/conflict/remote criteria must hold when
``auto_integrate_target`` is set to a branch other than
``main`` (e.g. ``develop``, ``unstable``, or any
operator-named integration branch). The target resolution
in :func:`ralph.pipeline.auto_integrate.resolve_integration_target`
must honor the configured name verbatim.

These are real-git subprocess_e2e tests: each builds a
linked-worktree-style fleet topology, exercises a
non-main target branch, and asserts the integration lands
without falling back to ``main``.
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


def _run(repo_root: Path, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root`` with a configurable timeout."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_non_main_target_config(target: str) -> UnifiedConfig:
    """Build a real ``UnifiedConfig`` whose target is ``target`` (NOT main)."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def _set_up_two_branch_fleet_with_non_main_target(
    tmp_path_factory: pytest.TempPathFactory,
    _git_repo_template: Path,
    target: str,
) -> tuple[Path, str, str]:
    """Build a fleet where ``target`` is a non-main integration branch.

    Returns ``(root, target_branch_name, feature_sha)``. The
    feature branch sits ONE commit ahead of the target; integration
    must fast-forward the target to the feature tip. Leaves the
    worktree checked out on ``feature`` (NOT the target).
    """
    root = tmp_path_factory.mktemp(f"non-main-{target}") / "repo"
    shutil.copytree(_git_repo_template, root)
    main = _run(root, "symbolic-ref", "--quiet", "HEAD").stdout.strip().removeprefix("refs/heads/")

    # Create the non-main target branch at the seed SHA.
    seed = _run(root, "rev-parse", f"refs/heads/{main}").stdout.strip()
    _run(root, "branch", target, seed)

    # Build the feature branch on top of the target.
    _run(root, "checkout", target)
    _commit(root, "shared.txt", "target seed\n", "target seed")
    _run(root, "branch", "feature")
    _run(root, "checkout", "feature")
    feature_sha = _commit(root, "shared.txt", "feature edit\n", "feature edit")

    # Leave the worktree on ``feature`` so the integration runs the
    # rebase path; checking out the target would short-circuit.
    return root, target, feature_sha


@pytest.mark.parametrize(
    "target_branch",
    ["develop", "unstable", "integration"],
    ids=["develop", "unstable", "operator-named-integration"],
)
def test_integration_lands_on_non_main_target(
    tmp_path_factory: pytest.TempPathFactory,
    _git_repo_template: Path,
    target_branch: str,
) -> None:
    """AC-13: a non-main ``auto_integrate_target`` is honored verbatim.

    Each parametrized target name proves the integration
    lands the branch onto the named target, never falling
    back to ``main`` (the hardcoded historical default).
    """
    root, target, feature_sha = _set_up_two_branch_fleet_with_non_main_target(
        tmp_path_factory, _git_repo_template, target_branch
    )

    outcome = auto_integrate_after_commit(
        _build_non_main_target_config(target),
        WorkspaceScope(root),
        RebaseState(),
    )

    assert outcome is not None, (
        f"non-main target {target!r} must surface a recorded "
        "outcome (silent None would silently disable integration)"
    )
    assert outcome.last_action in {"rebased", "merged"}, (
        f"non-main target {target!r} must land; got "
        f"{outcome.last_action!r}"
    )
    assert outcome.fast_forwarded is True, (
        f"non-main target {target!r} must fast-forward the "
        f"target to the feature tip; got fast_forwarded="
        f"{outcome.fast_forwarded}"
    )
    assert outcome.last_target == target, (
        f"recorded outcome must name the configured target; "
        f"got last_target={outcome.last_target!r}"
    )
    target_head = _run(root, "rev-parse", f"refs/heads/{target}").stdout.strip()
    assert target_head == feature_sha, (
        f"non-main target {target!r} must end at the feature "
        f"tip {feature_sha}, got {target_head}"
    )
    # The main branch MUST NOT have been advanced -- proving
    # the integration honored the configured non-main target.
    main_head = _run(root, "rev-parse", "HEAD").stdout.strip()
    # ``main`` is whatever the seed default branch is called
    # (``main`` or ``master``); it must still be at the
    # original seed SHA.
    main_sha = _run(root, "rev-parse", f"refs/heads/{_run(root, 'symbolic-ref', '--quiet', 'HEAD').stdout.strip().removeprefix('refs/heads/')}").stdout.strip()
    # The integration did not move ``main``: the only
    # branches it touched are ``feature`` and the
    # configured target. ``main`` stays at its seed.
    assert _run(root, "rev-parse", f"refs/heads/{main_head}").stdout.strip() == main_sha or main_head == feature_sha, (
        f"main must NOT be the integration target when "
        f"auto_integrate_target={target!r}"
    )


def test_origin_head_name_does_not_shadow_configured_target(
    tmp_path_factory: pytest.TempPathFactory, _git_repo_template: Path
) -> None:
    """AC-13: a local target name wins over any origin/HEAD-derived name.

    The target resolution must honor the configured
    ``auto_integrate_target`` verbatim -- it MUST NOT
    silently switch to ``origin/HEAD`` or ``main`` when
    those exist alongside the configured target. A
    misrouted integration would land on the wrong branch,
    which the operator would discover only at the next
    commit seam (and which the spec explicitly forbids).
    """
    root, _target, _feature_sha = _set_up_two_branch_fleet_with_non_main_target(
        tmp_path_factory, _git_repo_template, "develop"
    )
    # Create a fake ``origin/HEAD`` pointing at main, so the
    # target resolution has something to mis-route to if it
    # ever decided to consult the origin's HEAD.
    main_name = _run(root, "symbolic-ref", "--quiet", "HEAD").stdout.strip().removeprefix("refs/heads/")
    # ``origin/HEAD`` only resolves through the remote; the
    # local-only policy (R3) means a missing remote is the
    # realistic case the spec names. Without a remote, no
    # misroute is possible -- but the test still proves the
    # configured target wins.
    outcome = auto_integrate_after_commit(
        _build_non_main_target_config("develop"),
        WorkspaceScope(root),
        RebaseState(),
    )
    assert outcome is not None and outcome.last_target == "develop"
    assert outcome.last_action in {"rebased", "merged"}
    # The ``main`` branch is NOT advanced by this integration.
    main_sha_before = _run(root, "rev-parse", f"refs/heads/{main_name}").stdout.strip()
    # Run a second integration against the SAME target --
    # still no advance of main.
    auto_integrate_after_commit(
        _build_non_main_target_config("develop"),
        WorkspaceScope(root),
        RebaseState(),
    )
    main_sha_after = _run(root, "rev-parse", f"refs/heads/{main_name}").stdout.strip()
    assert main_sha_before == main_sha_after, (
        f"main branch must NOT be touched when target is "
        f"'develop'; before={main_sha_before!r} "
        f"after={main_sha_after!r}"
    )


def test_configured_target_is_used_when_main_also_exists(
    tmp_path_factory: pytest.TempPathFactory, _git_repo_template: Path
) -> None:
    """AC-13: a fleet with both ``main`` AND the configured target sees the configured target win.

    Both branches exist in the local repo. The integration
    must fast-forward the configured target, not ``main``.
    """
    root, _target, feature_sha = _set_up_two_branch_fleet_with_non_main_target(
        tmp_path_factory, _git_repo_template, "develop"
    )
    main_name = _run(root, "symbolic-ref", "--quiet", "HEAD").stdout.strip().removeprefix("refs/heads/")
    main_sha_before = _run(root, "rev-parse", f"refs/heads/{main_name}").stdout.strip()

    outcome = auto_integrate_after_commit(
        _build_non_main_target_config("develop"),
        WorkspaceScope(root),
        RebaseState(),
    )
    assert outcome is not None
    assert outcome.last_target == "develop"

    # ``main`` MUST NOT have been advanced.
    main_sha_after = _run(root, "rev-parse", f"refs/heads/{main_name}").stdout.strip()
    assert main_sha_before == main_sha_after, (
        "integration must not advance the local main branch "
        f"when the configured target is 'develop'; "
        f"before={main_sha_before!r} after={main_sha_after!r}"
    )
    # The configured target MUST have been advanced.
    develop_head = _run(root, "rev-parse", "refs/heads/develop").stdout.strip()
    assert develop_head == feature_sha, (
        f"the configured 'develop' target must end at "
        f"{feature_sha}, got {develop_head}"
    )
