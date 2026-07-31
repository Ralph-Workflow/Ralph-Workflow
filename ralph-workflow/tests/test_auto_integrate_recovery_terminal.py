"""Distributed real-git terminal-state recovery regressions.

These wrappers let the verification runner's file-level LPT scheduler distribute
real-git recovery work while the source helpers retain each scenario's setup
and observable assertions.
"""

from __future__ import annotations

import pytest

from tests.test_auto_integrate_recovery import (
    _test_integrate_once_propagates_terminal_violation_on_exception_path,
    _test_post_attempt_verify_abort_path_restores_pre_attempt_sha,
    _test_post_attempt_verify_clean_tree_after_land,
    _test_post_attempt_verify_in_progress_marker_violation_raises_loudly,
    _test_post_attempt_verify_passes_on_bare_rebase_head_with_no_active_rebase_dir,
    _test_post_attempt_verify_still_raises_when_rebase_head_accompanies_an_active_rebase_dir,
    _test_rebase_backup_ref_exists_during_attempt_and_is_cleaned_after,
    _test_rebase_backup_ref_observed_mid_attempt_then_cleaned_after_land,
    _test_seam_level_reclaim_lands_integration_without_recovery_preamble,
    _test_seam_level_reclaim_preserves_dirty_tree,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def test_rebase_backup_ref_exists_during_attempt_and_is_cleaned_after(tmp_git_repo: object) -> None:
    _test_rebase_backup_ref_exists_during_attempt_and_is_cleaned_after(tmp_git_repo)


def test_rebase_backup_ref_observed_mid_attempt_then_cleaned_after_land(tmp_git_repo: object) -> None:
    _test_rebase_backup_ref_observed_mid_attempt_then_cleaned_after_land(tmp_git_repo)


def test_post_attempt_verify_clean_tree_after_land(tmp_git_repo: object) -> None:
    _test_post_attempt_verify_clean_tree_after_land(tmp_git_repo)


def test_post_attempt_verify_in_progress_marker_violation_raises_loudly(tmp_git_repo: object) -> None:
    _test_post_attempt_verify_in_progress_marker_violation_raises_loudly(tmp_git_repo)


def test_post_attempt_verify_abort_path_restores_pre_attempt_sha(tmp_git_repo: object) -> None:
    _test_post_attempt_verify_abort_path_restores_pre_attempt_sha(tmp_git_repo)


def test_integrate_once_propagates_terminal_violation_on_exception_path(
    tmp_git_repo: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _test_integrate_once_propagates_terminal_violation_on_exception_path(tmp_git_repo, monkeypatch)


def test_seam_level_reclaim_lands_integration_without_recovery_preamble(tmp_git_repo: object) -> None:
    _test_seam_level_reclaim_lands_integration_without_recovery_preamble(tmp_git_repo)


def test_seam_level_reclaim_preserves_dirty_tree(tmp_git_repo: object) -> None:
    _test_seam_level_reclaim_preserves_dirty_tree(tmp_git_repo)


def test_post_attempt_verify_passes_on_bare_rebase_head_with_no_active_rebase_dir(
    tmp_git_repo: object,
) -> None:
    _test_post_attempt_verify_passes_on_bare_rebase_head_with_no_active_rebase_dir(tmp_git_repo)


def test_post_attempt_verify_still_raises_when_rebase_head_accompanies_an_active_rebase_dir(
    tmp_git_repo: object,
) -> None:
    _test_post_attempt_verify_still_raises_when_rebase_head_accompanies_an_active_rebase_dir(tmp_git_repo)
