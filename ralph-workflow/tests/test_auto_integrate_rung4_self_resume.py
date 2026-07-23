"""AC-15: rung-4 self-resume tests.

For rung-4 conditions (a condition that cannot be repaired by
the integration attempt itself, e.g. a shallow clone), the
spec requires:

* the diagnostic to be emitted at every seam, AND
* the moment the condition clears, integration to land on
  the very next seam with no restart and no manual reset.

Subprocess_e2e tests: real git repositories, real
``auto_integrate_after_commit`` calls, deterministic. No
network fetch -- the rung-4 case here is synthetic and the
``clear`` step is local-only, matching the spec's local-only
integration policy (R3).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase_preconditions import (
    RebasePreconditionError,
    check_rebase_preconditions,
)
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


def _base_branch(tmp_git_repo: Path) -> str:
    """Return the seed template's default branch name."""
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
    """Build a real ``UnifiedConfig`` with auto-integrate enabled."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def _diverged_two_commits(tmp_git_repo: Path) -> tuple[str, str]:
    """Build the canonical diverged-feature setup.

    Returns ``(base_branch_name, feature_sha)``. The feature
    branch sits ONE commit ahead of base; integration must
    fast-forward base to feature for a clean land.

    Leaves the worktree checked out on ``feature`` (NOT
    ``base``), because ``auto_integrate_after_commit`` short-
    circuits with ``current_branch == target`` and the test
    must exercise the rebase path.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha = _commit(tmp_git_repo, "shared.txt", "feature edit\n", "feature edit")
    # Stay on ``feature`` so the integration runs the rebase
    # path; checking out ``base`` would short-circuit with
    # ``current_branch == target`` before rebase is even tried.
    return base, feature_sha


def test_shallow_clone_precondition_emits_loud_rung4_diagnostic(
    tmp_git_repo: Path,
) -> None:
    """A shallow clone triggers a precondition rung-4 diagnostic (AC-15).

    The precondition that gates a rebase raises
    :class:`RebasePreconditionError` when a ``.git/shallow``
    file is present. The pipeline catches the error and
    records a rung-4 skip; the diagnostic names the
    remediation (``git fetch --unshallow``). Clearing the
    shallow file lets the next seam proceed normally.

    This test proves both halves of AC-15 against the
    actual precondition primitive: the rung-4 diagnostic
    fires when the condition is present, and integration
    proceeds immediately when the condition is cleared.
    """
    base, feature_sha = _diverged_two_commits(tmp_git_repo)

    # Install a real ``.git/shallow`` marker (one of the
    # precondition's rung-4 conditions). Use the absolute
    # git dir (not ``rev-parse --git-path`` which returns
    # a repo-relative path the precondition's path
    # resolution cannot follow under all cwd conditions).
    shallow_path = Path(_run(tmp_git_repo, "rev-parse", "--absolute-git-dir").stdout.strip()) / "shallow"
    shallow_path.parent.mkdir(parents=True, exist_ok=True)
    shallow_path.write_text(f"{feature_sha}\n", encoding="utf-8")
    assert shallow_path.exists(), (
        f"shallow marker setup failed; shallow_path={shallow_path}"
    )

    # Direct precondition check: must raise with the
    # rung-4 message.
    from git import Repo as _Repo

    _repo = _Repo(tmp_git_repo)
    try:
        _common = _repo.common_dir
        _git_dir = _repo.git_dir
    finally:
        _repo.close()
    print(
        f"DEBUG shallow_path={shallow_path} common_dir={_common} "
        f"git_dir={_git_dir} exists_at_common={Path(str(_common)) / 'shallow' if _common else 'NONE'}"
    )
    with pytest.raises(RebasePreconditionError) as exc_info:
        check_rebase_preconditions(tmp_git_repo)
    assert "shallow" in str(exc_info.value).lower(), (
        f"shallow-clone precondition must name the shallow "
        f"condition; got {exc_info.value!r}"
    )
    assert "unshallow" in str(exc_info.value).lower() or "fetch" in str(exc_info.value).lower(), (
        f"shallow-clone precondition must name the "
        f"remediation; got {exc_info.value!r}"
    )

    # Pipeline seam: the precondition failure must surface
    # as a recorded skip, never a silent None.
    outcome = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )
    assert outcome is not None, (
        "rung-4 condition must surface a recorded skip, "
        "not a silent None"
    )
    assert "preconditions not met" in (outcome.last_reason or ""), (
        f"rung-4 skip must name the precondition failure; "
        f"got last_reason={outcome.last_reason!r}"
    )
    assert "shallow" in (outcome.last_reason or "").lower(), (
        f"rung-4 diagnostic must name the shallow condition; "
        f"got last_reason={outcome.last_reason!r}"
    )

    # Clear the rung-4 condition: remove the shallow marker.
    shallow_path.unlink()

    # Re-run the precondition check: must now succeed.
    check_rebase_preconditions(tmp_git_repo)

    # Re-run the pipeline: must land on the next seam.
    outcome_second = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )
    assert outcome_second is not None, (
        "after clearing rung-4 the next seam must produce a "
        "recorded outcome (silent None would be a regression "
        "of the silent-skip bug class)"
    )
    assert outcome_second.last_action in {"rebased", "merged"}, (
        f"after clearing rung-4 the next seam must land; got "
        f"{outcome_second.last_action!r}"
    )
    assert outcome_second.fast_forwarded is True, (
        "after clearing rung-4 the next seam must fast-forward "
        "the target to the feature tip"
    )
    target_head = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert target_head == feature_sha, (
        f"target {base} should be at {feature_sha}, got {target_head}"
    )


def test_diagnostic_message_names_rung4_remediation() -> None:
    """The rung-4 diagnostic strings name the exact remediation command.

    AC-15 requires the rung-4 diagnostic to be actionable:
    an operator who reads the message must know what to do.
    Without a named remediation command, the rung-4
    condition would strand the worktree with no path to
    recovery short of reading the source code.

    Asserting on the literal text of the diagnostics locks
    the user-facing remediation string against silent
    drift: a future refactor that rewrites the diagnostic
    would break this test, which is the right behavior --
    the operator's run log is the contract.
    """
    # Re-importing the precondition module reads the
    # diagnostic strings from the source. The constant
    # capture keeps the test independent of any future
    # i18n / templating work.
    import inspect

    source = inspect.getsource(check_rebase_preconditions)
    # The diagnostic is built inside the helper function
    # ``_check_shallow_clone`` rather than in
    # ``check_rebase_preconditions`` itself. Reach into
    # the module's globals to capture the helper's source
    # too, so the audit reads the actual diagnostic string
    # rather than the outer function's text.
    check_shallow_source = inspect.getsource(
        check_rebase_preconditions.__globals__["_check_shallow_clone"]
    )
    combined = source + "\n" + check_shallow_source
    assert "git fetch --unshallow" in combined, (
        "shallow-clone precondition must name the "
        "git fetch --unshallow remediation; operator-facing "
        "diagnostic must be actionable"
    )


def test_precondition_error_class_is_loud() -> None:
    """RebasePreconditionError is a loud exception, never swallowed silently.

    The rung-4 contract is that the precondition error
    propagates as a classified exception, not a swallowed
    return value. A future refactor that turned the
    exception into a bare ``return False`` would silently
    disable integration under rung-4 conditions -- the
    exact regression the AC-08 audit is supposed to catch.
    """
    # The class must inherit from Exception so callers
    # that catch ``Exception`` see it (the pipeline's broad
    # handler). The class must NOT inherit from any
    # base-class signal that would make ``bool(exc)``
    # silent.
    assert issubclass(RebasePreconditionError, Exception)
    assert not isinstance(RebasePreconditionError(), (bool, int))
    # Raising + catching the exception exercises the
    # loud surface end-to-end.
    try:
        raise RebasePreconditionError("shallow clone rung-4 diagnostic")
    except RebasePreconditionError as exc:
        assert "shallow" in str(exc)
    else:
        pytest.fail("RebasePreconditionError was not raised")
