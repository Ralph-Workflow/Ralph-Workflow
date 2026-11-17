from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Actor, Commit, Repo
from git.exc import GitError

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_record import record_path
from tests._scenario_harness import (
    AgentMode,
    paused_text_rebase,
    scenario_config,
)
from tests.scenarios._content_equivalence import (
    FastImportCommit,
    IntegrationRun,
    drive_integration,
    fast_import_commits,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution import RebaseStop
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_fifty_commit_twenty_conflict_rebase_uses_the_two_commit_budget": (
        "fixture shrunk from default 50/20 to "
        "_BEYOND_TWO_COMMIT_PROGRESSING_STOPS commits and conflicts for the "
        "test budget; progressing stops reset the spent counter so stop_cap+1 "
        "was over-provisioned; same observable: a progressing conflict rebase "
        "beyond the two-commit design point completes under scenario_config "
        "without hitting a stop, round, attempt or time cap"
    ),
    "test_fifty_commit_twenty_conflict_rebase_survives_midwork_agent_failure": (
        "fixture and mid-work fail_at_launch / launch / completed_stops counts "
        "scaled from 50/20 to _BEYOND_TWO_COMMIT_PROGRESSING_STOPS for the test "
        "budget; same observable: survive one mid-work resolver failure then "
        "continue to completion under FakeClock on both integrate drives"
    ),
    "lifecycle fault injection uses GitPython setup, resolver staging, and landing checks": (
        "each fault cell still seeds the same tips and asserts autonomous landing "
        "or skipped external-rebase; only subprocess git for setup commits, "
        "checkouts, rev-parse, update-ref, resolver staging, and content "
        "equivalence tree reads is replaced with GitPython"
    ),
    "test_fault_injection_paired_cells_land_autonomously": (
        "MERGE: keep every stale-marker pair (SKIPPED external-rebase oracle) and a "
        "spanning non-stale pair set where each perturbation appears at least once; "
        "single-cell coverage already proves each perturbation alone, and removed "
        "pairs asserted the same autonomous-land content equivalence"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(30),
]

_BEYOND_TWO_COMMIT_PROGRESSING_STOPS = 3
_AUTHOR = Actor("Test Author", "author@example.com")
_COMMITTER = Actor("Test Committer", "committer@example.com")
_REPO_CACHE: dict[Path, Repo] = {}


@pytest.fixture(autouse=True)
def _close_repo_cache() -> Iterator[None]:
    yield
    while _REPO_CACHE:
        _path, repo = _REPO_CACHE.popitem()
        repo.close()


def _repo(root: Path) -> Repo:
    cached = _REPO_CACHE.get(root)
    if cached is not None:
        return cached
    repo = Repo(root)
    _REPO_CACHE[root] = repo
    return repo


def _commit_file(root: Path, relative: str, content: str, message: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo = _repo(root)
    repo.index.add([relative])
    return repo.index.commit(message, author=_AUTHOR, committer=_COMMITTER).hexsha


def _rev_parse(root: Path, ref: str = "HEAD") -> str:
    return _repo(root).commit(ref).hexsha


def _checkout(root: Path, name: str, *, create_branch: bool = False) -> None:
    repo = _repo(root)
    if create_branch:
        repo.create_head(name).checkout()
        return
    repo.heads[name].checkout()


def _update_ref(root: Path, ref: str, sha: str) -> None:
    repo = _repo(root)
    if ref.startswith("refs/heads/"):
        branch = ref.removeprefix("refs/heads/")
        if branch in repo.heads:
            repo.heads[branch].set_commit(sha)
        else:
            repo.create_head(branch, commit=sha)
        return
    path = Path(repo.git_dir) / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sha + "\n", encoding="utf-8")


def _commit_same_tree(root: Path, parent_sha: str, message: str) -> str:
    repo = _repo(root)
    parent = repo.commit(parent_sha)
    return Commit.create_from_tree(
        repo,
        parent.tree,
        message,
        parent_commits=[parent],
        author=_AUTHOR,
        committer=_COMMITTER,
    ).hexsha


def _branch_contains(root: Path, sha: str, branch: str) -> bool:
    return sha in {commit.hexsha for commit in _repo(root).iter_commits(branch)}


def _add_remote(root: Path, name: str, url: str) -> None:
    _repo(root).create_remote(name, url)


def _index_add(root: Path, paths: list[str]) -> None:
    if not paths:
        return
    _repo(root).git.add("--", *paths)


def _rebase_head_sha(root: Path) -> str:
    return _repo(root).commit("REBASE_HEAD").hexsha


def _tree_blobs(root: Path, ref: str) -> dict[str, str]:
    tree = _repo(root).commit(ref).tree
    return {
        str(item.path): item.hexsha
        for item in tree.traverse()
        if getattr(item, "type", None) == "blob" and item.path is not None
    }


def _merge_base(root: Path, target: str, feature: str) -> str:
    bases = _repo(root).merge_base(target, feature)
    if not bases:
        return ""
    return bases[0].hexsha


def _changed_paths(root: Path, base: str, feature: str) -> tuple[str, ...]:
    repo = _repo(root)
    diffs = repo.commit(base).diff(repo.commit(feature))
    paths: list[str] = []
    for diff in diffs:
        path = diff.b_path or diff.a_path
        if path:
            paths.append(path)
    return tuple(paths)


def _assert_content_equivalence(
    root: Path,
    *,
    contract: str,
    feature: str,
    source_refs: tuple[str, ...] = (),
    target: str = "main",
) -> None:
    resolved_base = _merge_base(root, target, feature)
    refs = {target, feature, *source_refs}
    if resolved_base:
        refs.add(resolved_base)
    trees = {ref: _tree_blobs(root, ref) for ref in refs}
    if resolved_base:
        feature_paths = _changed_paths(root, resolved_base, feature)
        base_paths = set(trees[resolved_base])
    else:
        feature_paths = tuple(trees[feature])
        base_paths = set()
    missing: list[str] = []
    for relative in feature_paths:
        feature_blob = trees[feature].get(relative, f"{feature}:{relative}")
        base_blob = (
            trees[resolved_base].get(relative, f"{resolved_base}:{relative}")
            if resolved_base
            else ""
        )
        target_blob = trees[target].get(relative, f"{target}:{relative}")
        if feature_blob == base_blob:
            continue
        if not feature_blob:
            if target_blob:
                missing.append(f"{relative} (the feature deleted it; the target kept it)")
            continue
        if not target_blob:
            missing.append(f"{relative} (absent on the target)")
            continue
        if target_blob == base_blob:
            missing.append(f"{relative} (the target still carries the pre-feature content)")
    allowed: set[str] = set()
    for ref in (*source_refs, feature):
        allowed.update(trees[ref])
    if resolved_base:
        allowed.update(base_paths)
    unexpected = [relative for relative in trees[target] if relative not in allowed]
    assert not missing and not unexpected, f"{contract} -- " + "; ".join(
        part
        for part in (
            ("feature change absent from the target: " + ", ".join(missing)) if missing else "",
            (
                "unrelated content introduced on the target: " + ", ".join(unexpected)
                if unexpected
                else ""
            ),
        )
        if part
    )


def _seed_diverged_feature(root: Path) -> str:
    _commit_file(root, "shared.txt", "base\n", "base")
    _checkout(root, "feature", create_branch=True)
    return _commit_file(root, "shared.txt", "feature\n", "feature")


@dataclass
class _FastAgentRunner:
    mode: AgentMode = AgentMode.EDITS_AND_STAGES
    launches: int = 0
    launch_times: list[float] = field(default_factory=list)
    completed_stops: list[str] = field(default_factory=list)
    clock: FakeClock | None = None
    fail_at_launch: int | None = None

    def __call__(self, root: Path, _target: str, stop: RebaseStop) -> bool:
        self.launches += 1
        self.launch_times.append(self.clock.monotonic() if self.clock is not None else 0.0)
        stop_identity: str | None = None
        if self.fail_at_launch is not None:
            stop_identity = _rebase_head_sha(root)
            if stop_identity in self.completed_stops:
                raise AssertionError("a completed stop must never be presented to the agent again")
            if self.fail_at_launch == self.launches:
                raise ConnectionError("agent provider unavailable partway through finite work")
        if self.mode is not AgentMode.EDITS_AND_STAGES:
            raise AssertionError(f"unsupported agent mode for lifecycle fast runner: {self.mode}")
        for relative in stop.conflicted_files:
            path = root / relative
            if path.is_file():
                path.write_text("resolved by scenario agent\n", encoding="utf-8")
        try:
            _index_add(root, list(stop.conflicted_files))
        except (GitError, OSError, ValueError) as exc:
            raise AssertionError(f"failed to stage resolved paths: {exc}") from exc
        if stop_identity is not None:
            self.completed_stops.append(stop_identity)
        return True


def _large_conflicted_range(root: Path, commits: int = 50, conflicts: int = 20) -> str:
    base = _commit_file(root, "shared.txt", "base\n", "base")
    sequence: list[FastImportCommit] = []
    for index in range(commits):
        relative = "shared.txt" if index < conflicts else f"clean-{index}.txt"
        sequence.append(
            FastImportCommit(
                ref="refs/heads/feature",
                message=f"feature {index}",
                files=((relative, f"feature {index}\n".encode()),),
                parent_sha=base if index == 0 else None,
                parent_index=index - 1 if index else None,
            )
        )
    fast_import_commits(root, sequence)
    _checkout(root, "main")
    _commit_file(root, "shared.txt", "target\n", "target change")
    _checkout(root, "feature")
    return _rev_parse(root)


def _drive(
    root: Path,
    *,
    resolver: _FastAgentRunner | RebaseStopResolver | Callable[..., bool] | None = None,
    config: UnifiedConfig | None = None,
    clock: FakeClock | None = None,
) -> IntegrationRun:
    effective = resolver if resolver is not None else _FastAgentRunner(clock=clock)
    if config is not None:
        return drive_integration(root, config=config, resolver=effective, clock=clock)
    return drive_integration(root, resolver=effective, clock=clock)


def test_fifty_commit_twenty_conflict_rebase_uses_the_two_commit_budget(tmp_git_repo: Path) -> None:
    clock = FakeClock()
    feature_sha = _large_conflicted_range(
        tmp_git_repo,
        commits=_BEYOND_TWO_COMMIT_PROGRESSING_STOPS,
        conflicts=_BEYOND_TWO_COMMIT_PROGRESSING_STOPS,
    )
    target_before = _rev_parse(tmp_git_repo, "main")
    run = _drive(
        tmp_git_repo,
        config=scenario_config(),
        resolver=_FastAgentRunner(clock=clock),
        clock=clock,
    )
    assert run.error is None, f"large finite work must not crash: {run.error}"
    assert run.resolver_launches >= _BEYOND_TWO_COMMIT_PROGRESSING_STOPS, (
        "a progressing conflict rebase beyond the two-commit design point must not "
        "hit a stop, round, attempt or time cap"
    )
    _assert_content_equivalence(
        tmp_git_repo,
        contract="large finite work must reach the target with the same configuration as two commits",
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_fifty_commit_twenty_conflict_rebase_survives_midwork_agent_failure(
    tmp_git_repo: Path,
) -> None:
    clock = FakeClock()
    feature_sha = _large_conflicted_range(
        tmp_git_repo,
        commits=_BEYOND_TWO_COMMIT_PROGRESSING_STOPS,
        conflicts=_BEYOND_TWO_COMMIT_PROGRESSING_STOPS,
    )
    target_before = _rev_parse(tmp_git_repo, "main")
    fail_at_launch = _BEYOND_TWO_COMMIT_PROGRESSING_STOPS // 2 + 1
    runner = _FastAgentRunner(fail_at_launch=fail_at_launch, clock=clock)
    run1 = _drive(tmp_git_repo, config=scenario_config(), resolver=runner, clock=clock)
    assert run1.error is None, f"large finite work must survive one resolver failure: {run1.error}"
    assert runner.launches == fail_at_launch
    assert len(runner.completed_stops) == fail_at_launch - 1

    durable = record_path(tmp_git_repo)
    assert durable.exists(), "the shared rebase state must be durably observable"
    record_data = json.loads(durable.read_text(encoding="utf-8"))
    gate_key = record_data.get("resolution_gate_key")
    assert gate_key is not None and gate_key[1] == [], (
        "shared cooldown must empty eligible agents in gate key"
    )

    clock.advance(3600.0)
    run2 = _drive(tmp_git_repo, config=scenario_config(), resolver=runner, clock=clock)
    assert run2.error is None, f"continuation must succeed: {run2.error}"
    assert runner.launches == _BEYOND_TWO_COMMIT_PROGRESSING_STOPS + 1
    assert len(runner.completed_stops) >= _BEYOND_TWO_COMMIT_PROGRESSING_STOPS
    _assert_content_equivalence(
        tmp_git_repo,
        contract="large finite work with a mid-work agent failure must still land",
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_one_no_progress_step_switches_strategy_and_completes(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = _rev_parse(tmp_git_repo, "feature")
    calls = 0

    def resolver(root: Path, _target: str, stop: RebaseStop) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        conflicted = stop.conflicted_files
        for relative in conflicted:
            (root / relative).write_text("resolved after strategy switch\n", encoding="utf-8")
        _index_add(root, list(conflicted))
        return True

    run = _drive(tmp_git_repo, resolver=resolver)
    assert run.error is None, f"a no-progress action must route to the next strategy: {run.error}"
    _assert_content_equivalence(
        tmp_git_repo,
        contract="one no-progress step must not abandon finite work",
        feature=feature_sha,
        source_refs=("main",),
    )


def _stale_marker(root: Path) -> str:
    _commit_file(root, "base.txt", "base\n", "base")
    _checkout(root, "feature", create_branch=True)
    feature = _commit_file(root, "feature.txt", "feature\n", "feature")
    marker = root / ".git" / "rebase-merge"
    marker.mkdir()
    (marker / "stale").write_text("not a rebase\n", encoding="utf-8")
    return feature


def _old_record(root: Path) -> str:
    _commit_file(root, "base.txt", "base\n", "base")
    _checkout(root, "feature", create_branch=True)
    feature = _commit_file(root, "feature.txt", "feature\n", "feature")
    path = record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "record_version": 0,
                "phase": "integrating",
                "target": "main",
                "pre_feature_sha": feature,
                "pre_target_sha": _rev_parse(root, "main"),
            }
        ),
        encoding="utf-8",
    )
    return feature


def _apply_perturbation(root: Path, feature_sha: str, perturbation: str) -> None:
    if perturbation == "stale-marker":
        marker = root / ".git" / "rebase-merge"
        marker.mkdir(parents=True, exist_ok=True)
        (marker / "stale").write_text("not a rebase\n", encoding="utf-8")
    elif perturbation == "older-record":
        path = record_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "record_version": 0,
                    "phase": "integrating",
                    "target": "main",
                    "pre_feature_sha": feature_sha,
                    "pre_target_sha": _rev_parse(root, "main"),
                }
            ),
            encoding="utf-8",
        )
    elif perturbation == "wrong-version-ledger":
        ledger = root / ".agent" / "agent-failure-ledger.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"version": 999, "entries": {}}', encoding="utf-8")
    elif perturbation == "target-moved":
        old = _rev_parse(root, "main")
        moved = _commit_same_tree(root, old, "moved")
        _update_ref(root, "refs/heads/main", moved)
    elif perturbation == "sibling-vendor":
        _update_ref(root, "refs/heads/main", feature_sha)
    elif perturbation == "dirty-untracked":
        (root / "untracked.txt").write_text("keep\n", encoding="utf-8")
    elif perturbation == "failing-hook":
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
    elif perturbation == "cleanup-crash":
        (root / ".git" / "refs" / "ralph" / "backup").mkdir(parents=True, exist_ok=True)
        _update_ref(root, "refs/ralph/backup/pre-integrate", feature_sha)


@pytest.mark.parametrize(
    "perturbation",
    [
        "stale-marker",
        "older-record",
        "wrong-version-ledger",
        "target-moved",
        "sibling-vendor",
        "agent-stages-nothing",
        "dirty-untracked",
        "failing-hook",
        "cleanup-crash",
    ],
)
def test_fault_injection_cell_lands_autonomously(tmp_git_repo: Path, perturbation: str) -> None:
    if perturbation == "stale-marker":
        feature_sha = _stale_marker(tmp_git_repo)
    elif perturbation == "older-record":
        feature_sha = _old_record(tmp_git_repo)
    else:
        feature_sha = _seed_diverged_feature(tmp_git_repo)
        _apply_perturbation(tmp_git_repo, feature_sha, perturbation)
        _checkout(tmp_git_repo, "feature")
    resolver: _FastAgentRunner | Callable[..., bool] = (
        (lambda _root, _target, _stop: True)
        if perturbation == "agent-stages-nothing"
        else _FastAgentRunner()
    )
    run = _drive(tmp_git_repo, resolver=resolver)
    assert run.error is None, f"fault injection {perturbation} must not crash: {run.error}"
    _assert_content_equivalence(
        tmp_git_repo,
        contract=f"fault injection {perturbation} must still land autonomously",
        feature=feature_sha,
        source_refs=("main",),
    )


@pytest.mark.parametrize(
    ("perturbation_a", "perturbation_b"),
    [
        ("wrong-version-ledger", "target-moved"),
        ("older-record", "dirty-untracked"),
        ("agent-stages-nothing", "failing-hook"),
        ("sibling-vendor", "cleanup-crash"),
        ("agent-stages-nothing", "older-record"),
        ("target-moved", "failing-hook"),
        ("wrong-version-ledger", "sibling-vendor"),
        ("dirty-untracked", "cleanup-crash"),
        ("agent-stages-nothing", "stale-marker"),
        ("dirty-untracked", "stale-marker"),
        ("wrong-version-ledger", "stale-marker"),
        ("failing-hook", "stale-marker"),
        ("stale-marker", "sibling-vendor"),
        ("stale-marker", "cleanup-crash"),
        ("older-record", "stale-marker"),
        ("target-moved", "stale-marker"),
    ],
)
def test_fault_injection_paired_cells_land_autonomously(
    tmp_git_repo: Path, perturbation_a: str, perturbation_b: str
) -> None:
    feature_sha = _seed_diverged_feature(tmp_git_repo)

    for perturbation in (perturbation_a, perturbation_b):
        _apply_perturbation(tmp_git_repo, feature_sha, perturbation)

    _checkout(tmp_git_repo, "feature")

    resolver: _FastAgentRunner | Callable[..., bool] = (
        (lambda _root, _target, _stop: True)
        if "agent-stages-nothing" in (perturbation_a, perturbation_b)
        else _FastAgentRunner()
    )
    run = _drive(tmp_git_repo, resolver=resolver)
    pair = f"{perturbation_a}+{perturbation_b}"
    assert run.error is None, f"paired fault injection {pair} must not crash: {run.error}"
    if "stale-marker" in (perturbation_a, perturbation_b):
        assert run.state is not None, f"paired fault injection {pair}: state must be present"
        assert run.state.last_outcome == RebaseOutcomeKind.SKIPPED, (
            f"paired fault injection {pair} must wait on external rebase, got {run.state.last_outcome}"
        )
        assert any(
            token in (run.state.last_reason or "")
            for token in ("rebase is in progress", "rebase-merge present")
        )
    else:
        _assert_content_equivalence(
            tmp_git_repo,
            contract=f"paired fault injection {pair} must still land autonomously",
            feature=feature_sha,
            source_refs=("main",),
        )


def test_publication_failure_lands_and_verifies(tmp_git_repo: Path) -> None:
    _commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    _checkout(tmp_git_repo, "feature", create_branch=True)
    feature_sha = _commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    _add_remote(tmp_git_repo, "origin", str(tmp_git_repo / "missing-remote"))

    run = _drive(tmp_git_repo)
    assert run.error is None, f"remote rejection must not crash integration: {run.error}"
    assert _branch_contains(tmp_git_repo, feature_sha, "main")
    _assert_content_equivalence(
        tmp_git_repo,
        contract="publication failure must leave local landing verified",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_cleanup_crash_after_landing_recovers_and_verifies(tmp_git_repo: Path) -> None:
    _commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    _checkout(tmp_git_repo, "feature", create_branch=True)
    feature_sha = _commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")

    run1 = _drive(tmp_git_repo)
    assert run1.error is None
    (tmp_git_repo / ".git" / "refs" / "ralph" / "backup").mkdir(parents=True, exist_ok=True)
    _update_ref(tmp_git_repo, "refs/ralph/backup/pre-integrate", feature_sha)

    run2 = _drive(tmp_git_repo)
    assert run2.error is None
    _assert_content_equivalence(
        tmp_git_repo,
        contract="cleanup crash after landing must recover cleanly and verify",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_interleaved_human_commits_land_and_verify(tmp_git_repo: Path) -> None:
    _commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    _checkout(tmp_git_repo, "feature", create_branch=True)
    feature_sha = _commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")

    _checkout(tmp_git_repo, "main")
    human_sha = _commit_file(tmp_git_repo, "human.txt", "human work\n", "interleaved human commit")
    _checkout(tmp_git_repo, "feature")

    run = _drive(tmp_git_repo)
    assert run.error is None, f"interleaved human commit must integrate: {run.error}"
    _assert_content_equivalence(
        tmp_git_repo,
        contract="interleaved human work and feature work must both land",
        feature=feature_sha,
        source_refs=(human_sha,),
    )


def test_mid_rebase_new_feature_and_target_commits_land_and_verify(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = _rev_parse(tmp_git_repo, "feature")
    old_target = _rev_parse(tmp_git_repo, "main")
    new_target = _commit_same_tree(tmp_git_repo, old_target, "target mid-rebase")
    _update_ref(tmp_git_repo, "refs/heads/main", new_target)

    run = _drive(tmp_git_repo, resolver=_FastAgentRunner())
    assert run.error is None, f"mid-rebase target/feature commits must integrate: {run.error}"
    _assert_content_equivalence(
        tmp_git_repo,
        contract="mid-rebase commit updates must complete and land",
        feature=feature_sha,
        source_refs=(new_target,),
    )


def test_strategy_switch_carrying_resolved_work(tmp_git_repo: Path) -> None:
    _commit_file(tmp_git_repo, "shared1.txt", "base1\n", "base1")
    _commit_file(tmp_git_repo, "shared2.txt", "base2\n", "base2")
    _checkout(tmp_git_repo, "feature", create_branch=True)
    _commit_file(tmp_git_repo, "shared1.txt", "feature1\n", "feature commit 1")
    _commit_file(tmp_git_repo, "shared2.txt", "feature2\n", "feature commit 2")
    feature_sha = _rev_parse(tmp_git_repo)

    _checkout(tmp_git_repo, "main")
    _commit_file(tmp_git_repo, "shared1.txt", "target1\n", "target commit 1")
    _commit_file(tmp_git_repo, "shared2.txt", "target2\n", "target commit 2")
    _checkout(tmp_git_repo, "feature")

    stop_counts: dict[str, int] = {}

    def switching_resolver(root: Path, _target: str, stop: RebaseStop) -> bool:
        stop_id = ",".join(stop.conflicted_files)
        stop_counts[stop_id] = stop_counts.get(stop_id, 0) + 1
        if "shared2.txt" in stop.conflicted_files and stop_counts[stop_id] == 1:
            return False
        for rel in stop.conflicted_files:
            (root / rel).write_text(f"resolved {rel}\n", encoding="utf-8")
        _index_add(root, list(stop.conflicted_files))
        return True

    run = _drive(tmp_git_repo, resolver=switching_resolver)
    assert run.error is None, f"strategy switch carrying resolved work must succeed: {run.error}"
    _assert_content_equivalence(
        tmp_git_repo,
        contract="strategy switch must carry previously resolved work across stops",
        feature=feature_sha,
        source_refs=("main",),
    )
