"""Real-repository fail-closed contracts for the auto-integration seam.

Companion to :mod:`tests.test_auto_integrate_fail_closed`, which pins the
same rules for the primitives that never open a repository and therefore
stay in the default (budget-tracked) suite. The cases here CANNOT: both
``auto_integrate_after_commit`` and ``recover_incomplete_integration``
open a real GitPython ``Repo`` (via
``_current_branch_or_detached_marker``) and run real git subprocesses, so
the whole file carries the ``subprocess_e2e`` marker.

Two regressions are pinned:

* A failed ``get_head_sha`` used to escape the auto-integrate skip table
  into the caller's broad ``except Exception``, where it was recorded as
  an opaque ``unexpected failure`` naming neither the operation nor the
  repository. The recorded skip must name the real error.
* The crash-recovery preamble used to ask ``merge_in_progress``, whose
  boolean collapses "git could not be asked" into "no merge in
  progress". That made the abort a silent no-op and let the durable
  integration record be cleared while a ``MERGE_HEAD`` may still have
  been on disk.

The paired POSITIVE cases -- a readable clean state recovers and clears
the durable record -- are already pinned by
``tests/test_auto_integrate_recovery.py`` against real interrupted
rebases, so they are not duplicated here: this suite runs under a hard
60-second wall-clock budget.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_UNKNOWN, branch_sha
from ralph.git.operations import GitOperationError
from ralph.pipeline.auto_integrate import (
    IntegrationRecord,
    auto_integrate_after_commit,
    recover_incomplete_integration,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]

_RECORD_RELPATH = Path(".agent") / "auto_integrate_in_progress.json"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _base_branch(repo_root: Path) -> str:
    """Return the seed template's default branch name."""
    out = _run(repo_root, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _head_sha(repo_root: Path) -> str:
    """Return the current ``HEAD`` SHA."""
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    """Write+stage+commit ``filename`` and return the resulting HEAD SHA."""
    (repo_root / filename).write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _head_sha(repo_root)


def _config(target: str) -> UnifiedConfig:
    """Build a real ``UnifiedConfig`` pinning the integration target."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )


def _write_record(repo_root: Path, record: IntegrationRecord) -> Path:
    """Write the durable integration record and return its path."""
    record_file = repo_root / _RECORD_RELPATH
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    return record_file


def test_auto_integrate_regression_head_read_failure_names_the_error(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed HEAD read is a recorded skip naming the underlying error."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feature.txt", "feature\n", "feature change")

    import ralph.pipeline.auto_integrate as auto_integrate_module

    def _failing_head_sha(repo_root: Path | str) -> str:
        raise GitOperationError("get_head_sha", "simulated HEAD read failure")

    monkeypatch.setattr(
        auto_integrate_module, "get_head_sha", _failing_head_sha
    )

    outcome = auto_integrate_after_commit(
        _config(base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    reason = outcome.last_reason or ""
    assert "HEAD read failed" in reason
    assert "Git get_head_sha failed" in reason, (
        "the recorded skip must name the real operation, not an opaque"
        f" 'unexpected failure'; got {reason!r}"
    )
    assert "simulated HEAD read failure" in reason
    assert outcome.last_target == base
    assert outcome.fast_forwarded is False


def test_recovery_regression_unreadable_merge_state_retains_the_record(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable merge state must not be recovered as a clean tree."""
    base = _base_branch(tmp_git_repo)
    pre_feature_sha = _head_sha(tmp_git_repo)
    record_file = _write_record(
        tmp_git_repo,
        IntegrationRecord(
            phase="integrating",
            target=base,
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=branch_sha(tmp_git_repo, base) or "",
        ),
    )
    import ralph.pipeline.auto_integrate_recovery as recovery_module

    def _unreadable_merge_state(repo_root: Path | str) -> str:
        return MERGE_STATE_UNKNOWN

    monkeypatch.setattr(
        recovery_module, "merge_state", _unreadable_merge_state
    )

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None
    assert outcome.last_action == "skipped", (
        "an unreadable merge state must not be reported as a completed"
        f" recovery; got {outcome.last_action!r}"
    )
    assert "retained for retry" in (outcome.last_reason or "")
    assert outcome.last_target == base
    assert record_file.exists(), (
        "the durable record must be retained while a MERGE_HEAD we cannot"
        " see may still be on disk"
    )


def test_recovery_regression_unreadable_merge_state_holds_an_integrated_record(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The integrated (fast-forward) phase fails closed the same way.

    The bail-out happens BEFORE the fast-forward continuation, so no
    feature commit is needed to reach it -- and the mainline must be
    exactly where it started when the call returns.
    """
    base = _base_branch(tmp_git_repo)
    base_sha = _head_sha(tmp_git_repo)
    record_file = _write_record(
        tmp_git_repo,
        IntegrationRecord(
            phase="integrated",
            target=base,
            pre_feature_sha=base_sha,
            pre_target_sha=base_sha,
            integrated_feature_sha=base_sha,
        ),
    )
    import ralph.pipeline.auto_integrate_recovery as recovery_module

    def _unreadable_merge_state(repo_root: Path | str) -> str:
        return MERGE_STATE_UNKNOWN

    monkeypatch.setattr(
        recovery_module, "merge_state", _unreadable_merge_state
    )

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "retained for retry" in (outcome.last_reason or "")
    assert record_file.exists()
    assert branch_sha(tmp_git_repo, base) == base_sha, (
        "the mainline must not be moved while the merge state is unreadable"
    )
