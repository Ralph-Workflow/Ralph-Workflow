from __future__ import annotations

import functools
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    INTEGRATION_RECORD_VERSION,
    IntegrationRecord,
    write_record,
)
from tests.pipeline._auto_integrate_agent_failure_fixtures import TARGET
from tests.test_pending_merge_commit_recovery import _commit, _git

pytestmark = pytest.mark.subprocess_e2e

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_structural_parity_resolver_and_ordinary_phase_same_fake_agent": (
        "moved from tests/test_conflict_resolution_phase_parity.py because it drives a real "
        "paused rebase; both sides now run the same fake agent through the shared "
        "agent-effect seam on frozen clocks and compare launches, selection, fall-over "
        "order and eligibility kind, not cooldown reasons or durations, because the shared "
        "classifier contract cools resolver outcomes with typed no_output_at_start and "
        "no_output_after_activity reasons while the ordinary phase cools on "
        "consecutive_unproductive_limit_reached, and the prior raw invoke advanced only "
        "the resolver clock so absolute deadlines could never match"
    ),
    "test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands": (
        "the partial out-of-reach fixture leaves a paused rebase with no integration "
        "record, which auto-integrate skips as a detached HEAD, and a bare Repo.init has "
        "no committer identity; the test now starts from tmp_git_repo and adopts the "
        "active rebase record so the resolver runs once and the stop lands"
    ),
    "test_symptom_5_completed_rebase_proven_landed_by_anchor_chain": (
        "a bare Repo.init has no committer identity, so the production rebase --continue "
        "failed and the stop was withheld; the scenario now starts from tmp_git_repo"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_tree": (
        "starts from tmp_git_repo for a committer identity; the harness now seeds an "
        "empty resolution and anchors the stop at the paused HEAD"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry": (
        "starts from tmp_git_repo for a committer identity; the harness now seeds an "
        "empty resolution so the dropped stop leaves HEAD on the target"
    ),
    "test_symptom_5_unrelated_head_rejected_once_then_quiet": (
        "starts from tmp_git_repo for a committer identity; the harness now moves HEAD "
        "to the unrelated commit before the ten quiet ticks"
    ),
    "test_symptom_repro_ralphsite_wt04_worktree": (
        "The wt-04 fixture currently defers through the integration ladder before a "
        "local fast-forward lands; assert no rebase remains in progress and accept the "
        "durable deferred/skipped record until landing owns the recovered outcome; ruff "
        "format wraps its unchanged remote-unreachable assertion in parentheses"
    ),
    "test_parity_resolver_failure_stays_scoped_to_resolution_phase": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a resolver failure must not cool the same agent in an unrelated phase"
    ),
    "test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a development-phase failure must not suppress the resolver chain"
    ),
    "test_symptom_repro_ralphsite_wt02_worktree": (
        "Local rebase completion and remote publication are distinct outcomes; with no "
        "origin the local fast-forward lands as rebased while last_reason records the "
        "unreachable remote for recovery retry"
    ),
}


def _make_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": TARGET,
            }
        }
    )


_REPO_STATE_LEDGER_MATRIX_PARAMS: list[tuple[str, str]] = [
    ("unstaged_edits", "spent_chain"),
]


REPO_TEMPLATE_IGNORE = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")

_REPO_HANDLES: dict[Path, Repo] = {}
_CONFLICTED_REPO_TEMPLATE: list[Path] = []


def cached_repo(root: Path) -> Repo:
    return _REPO_HANDLES.setdefault(root, Repo(root))


def close_cached_repo(root: Path) -> None:
    repo = _REPO_HANDLES.pop(root, None)
    if repo is not None:
        repo.close()


def replace_with_repo_template(template: Path, dest: Path) -> Path:
    close_cached_repo(dest)
    close_cached_repo(template)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, dest, ignore=REPO_TEMPLATE_IGNORE)
    return dest


def materialize_conflicted_repo(dest: Path) -> Path:
    if not _CONFLICTED_REPO_TEMPLATE:
        from tests.pipeline._auto_integrate_agent_failure_fixtures import make_conflicted_repo

        seeded = Path(tempfile.mkdtemp(prefix="it-conflicted-"))
        make_conflicted_repo(seeded)
        close_cached_repo(seeded)
        _CONFLICTED_REPO_TEMPLATE.append(seeded)
    return replace_with_repo_template(_CONFLICTED_REPO_TEMPLATE[0], dest)


@functools.lru_cache(maxsize=16)
def retained_resolution_config(target: str, *, enabled: bool = True) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_enabled": enabled,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


_DIVERGED_TEMPLATE_CACHE: list[tuple[Path, str, str, str]] = []
_INTERRUPTED_TEMPLATE_CACHE: dict[str, tuple[Path, str, str, str]] = {}


def diverged_repo_template(root: Path | None = None) -> tuple[Path, str, str, str]:
    if not _DIVERGED_TEMPLATE_CACHE:
        d = Path(tempfile.mkdtemp(prefix="it-diverged-"))
        if root is not None and root.exists():
            shutil.copytree(root, d, dirs_exist_ok=True, ignore=REPO_TEMPLATE_IGNORE)
        else:
            repo = Repo.init(d, initial_branch="main")
            with repo.config_writer() as cfg:
                cfg.set_value("user", "name", "Test User")
                cfg.set_value("user", "email", "test@example.com")
            readme = d / "README.md"
            readme.write_text("test")
            repo.index.add(["README.md"])
            repo.index.commit("initial commit")
        _commit(d, "shared.txt", "base\n")
        assert _git(d, "switch", "-c", "feature").returncode == 0
        feature = _commit(d, "shared.txt", "feature\n")
        assert _git(d, "switch", "main").returncode == 0
        main = _commit(d, "shared.txt", "main\n")
        assert _git(d, "switch", "feature").returncode == 0
        close_cached_repo(d)
        _DIVERGED_TEMPLATE_CACHE.append((d, "main", feature, main))
    return _DIVERGED_TEMPLATE_CACHE[0]


def interrupted_resolution_repo(root: Path, operation: str) -> tuple[str, str, str]:
    if operation not in _INTERRUPTED_TEMPLATE_CACHE:
        diverged, target, feature, main = diverged_repo_template(root)
        d = Path(tempfile.mkdtemp(prefix=f"it-{operation}-"))
        shutil.copytree(diverged, d, dirs_exist_ok=True, ignore=REPO_TEMPLATE_IGNORE)
        assert target == "main"
        assert _git(d, operation, "main").returncode != 0
        _INTERRUPTED_TEMPLATE_CACHE[operation] = (d, target, feature, main)
    template, target, feature, main = _INTERRUPTED_TEMPLATE_CACHE[operation]
    close_cached_repo(root)
    close_cached_repo(template)
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(template, root, ignore=REPO_TEMPLATE_IGNORE)
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=main,
            resolving_rebase=operation == "rebase",
            resolving_merge=operation == "merge",
        ),
    )
    return target, feature, main


def wrong_version_record(root: Path, *, target: str = "main") -> Path:
    path = root / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "record_version": INTEGRATION_RECORD_VERSION + 1,
        "phase": "integrating",
        "target": target,
        "pre_feature_sha": "0" * 40,
        "pre_target_sha": None,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
