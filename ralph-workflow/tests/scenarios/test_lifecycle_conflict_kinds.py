from __future__ import annotations

import functools
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
from git import Repo
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.gitlink_preference import GitlinkPreference
from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    git,
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
)
from tests.scenarios._content_equivalence import (
    IntegrationRun,
    assert_content_equivalence,
    drive_integration,
    lifecycle_config,
    snapshot_uncommitted,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.config.models import UnifiedConfig

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(12),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "paused_and_gitlink_template_reuse": (
        "resolved-unstaged and gitlink cases materialize cached paused topologies"
    ),
    "FakeClock_on_integrate_drives": (
        "conflict-kind integrate drives use FakeClock; landing asserts unchanged"
    ),
    "GitPython_sha_and_contains_reads": (
        "tip and branch-contains checks use GitPython instead of git subprocess"
    ),
    "conflict_kinds_single_stop": (
        "append-only, delete-modify, and binary conflicts still land in one stop via paired "
        "multi-file commits instead of three serial conflict drives; content-equivalence on "
        "each path unchanged"
    ),
    "dirty_tracked_and_untracked_one_drive": (
        "tracked and untracked dirty work are preserved together in one integrate drive; "
        "both snapshot asserts unchanged versus the prior parametrized pair"
    ),
    "test_gitlink_conflict_without_a_configured_policy_uses_the_default_and_lands_quietly": (
        "the same mixed text-and-gitlink rebase runs with no configured gitlink policy; "
        "the built-in default picks the pointer, the review record carries the three "
        "stage SHAs and the alternatives considered, the branch lands, and ten further "
        "supervisor ticks emit nothing further and launch no agent"
    ),
}

_GITLINK_PATH = "vendor/link"
_SUPERVISOR_TICKS = 10
_DEFAULT_ALTERNATIVES = ("unchanged-from-base", "ours", "theirs")
_REVIEW_PREFIX = "conflict_resolution: gitlink policy "
_REVIEW_FIELDS = ("path", "policy", "base", "ours", "theirs", "selected_side", "selected")


class _GitlinkReview(NamedTuple):
    path: str
    policy: str
    base: str
    ours: str
    theirs: str
    selected_side: str
    selected: str
    alternatives: tuple[str, ...]


def _link_git_objects_or_copy(src: str, dst: str) -> None:
    src_path = Path(src)
    parts = src_path.parts
    if ".git" in parts:
        git_index = parts.index(".git")
        rest = parts[git_index + 1 :]
        if rest and rest[0] == "objects" and len(rest) >= 2:
            try:
                os.link(src, dst)
            except OSError:
                pass
            else:
                return
    shutil.copy2(src, dst)


def _seed_repo(base: Path) -> None:
    readme = base / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(base, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    repo.close()


@functools.lru_cache(maxsize=1)
def _paused_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-kinds-paused-"))
    _seed_repo(base)
    paused_text_rebase(base)
    return base


@functools.lru_cache(maxsize=1)
def _gitlink_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-kinds-gitlink-"))
    _seed_repo(base)
    paused_text_and_gitlink_rebase(base)
    return base


def _materialize(template: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        template,
        dest,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )
    return dest


def _sha(root: Path, ref: str) -> str:
    repo = Repo(root)
    try:
        return repo.commit(ref).hexsha
    finally:
        repo.close()


def _contains(root: Path, commit: str, branch: str = "main") -> bool:
    repo = Repo(root)
    try:
        return repo.is_ancestor(commit, branch)
    finally:
        repo.close()


def _write_commit(repo: Repo, relative: str, content: str, message: str) -> str:
    root = Path(repo.working_tree_dir or ".")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relative])
    return repo.index.commit(message).hexsha


def _drive(
    root: Path,
    *,
    resolver: FakeAgentRunner | None = None,
    config: UnifiedConfig | None = None,
    clock: FakeClock | None = None,
) -> IntegrationRun:
    effective_clock = clock if clock is not None else FakeClock()
    effective = resolver if resolver is not None else FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    if effective.clock is None:
        effective.clock = effective_clock
    return drive_integration(root, config=config, resolver=effective, clock=effective_clock)


def _gitlink_stage_shas(root: Path) -> tuple[str, str, str]:
    entries: dict[int, str] = {}
    for line in git(root, "ls-files", "-u", "--", _GITLINK_PATH).splitlines():
        metadata, _separator, _relative = line.partition("\t")
        fields = metadata.split()
        if len(fields) == 3:
            entries[int(fields[2])] = fields[1]
    shas = (entries.get(1, ""), entries.get(2, ""), entries.get(3, ""))
    if not all(shas):
        raise AssertionError(f"expected three gitlink stages for {_GITLINK_PATH}, got {shas}")
    return shas


def _gitlink_pointer(root: Path, ref: str) -> str:
    output = git(root, "ls-tree", ref, "--", _GITLINK_PATH)
    metadata, _separator, _relative = output.partition("\t")
    fields = metadata.split()
    if len(fields) != 3:
        raise AssertionError(f"expected one gitlink entry for {_GITLINK_PATH}, got {output!r}")
    return fields[2]


def _parse_alternatives(raw: str) -> tuple[str, ...]:
    inner = raw.strip().removeprefix("(").removesuffix(")").strip()
    if not inner:
        return ()
    return tuple(part.strip().strip("'\"") for part in inner.split(",") if part.strip())


def _gitlink_review(message: str) -> _GitlinkReview | None:
    if not message.startswith(_REVIEW_PREFIX):
        return None
    tokens = message[len(_REVIEW_PREFIX) :].split(" ")
    fields: dict[str, str] = {}
    for token in tokens[: len(_REVIEW_FIELDS)]:
        key, _separator, value = token.partition("=")
        fields[key] = value
    if set(fields) != set(_REVIEW_FIELDS):
        return None
    key, _separator, value = " ".join(tokens[len(_REVIEW_FIELDS) :]).partition("=")
    if key != "alternatives":
        return None
    return _GitlinkReview(
        path=fields["path"],
        policy=fields["policy"],
        base=fields["base"],
        ours=fields["ours"],
        theirs=fields["theirs"],
        selected_side=fields["selected_side"],
        selected=fields["selected"],
        alternatives=_parse_alternatives(value),
    )


@contextmanager
def _captured_messages() -> Iterator[list[str]]:
    messages: list[str] = []

    def sink(message: object) -> None:
        messages.append(str(message))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def test_resolved_but_unstaged_file_is_finished_without_an_agent(tmp_path: Path) -> None:
    root = _materialize(_paused_rebase_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    (root / "shared.txt").write_text("resolved by operator\n", encoding="utf-8")
    run = _drive(root)
    assert run.resolver_launches == 0, (
        "resolved-but-unstaged content must be staged mechanically before launching an agent"
    )
    assert_content_equivalence(
        root,
        contract="resolved-but-unstaged content must land",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_gitlink_vendor_conflict_uses_a_policy_pointer_and_lands(tmp_path: Path) -> None:
    root = _materialize(_gitlink_rebase_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    run = _drive(root, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))
    assert run.error is None, f"gitlink policy handling must not crash: {run.error}"
    assert_content_equivalence(
        root,
        contract="a gitlink conflict must land with the policy-selected pointer",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_gitlink_conflict_without_a_configured_policy_uses_the_default_and_lands_quietly(
    tmp_path: Path,
) -> None:
    root = _materialize(_gitlink_rebase_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    stage_shas = _gitlink_stage_shas(root)
    config = lifecycle_config()
    assert "gitlink_policy" not in config.conflict_resolution.model_dump(exclude_unset=True), (
        "the gitlink policy must be unavailable: nothing may configure a pointer preference"
    )
    assert (
        config.conflict_resolution.gitlink_policy.pointer_preference
        is GitlinkPreference.VENDOR_IMMUTABLE
    ), "the built-in default pointer preference must apply"
    clock = FakeClock()
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    runner.clock = clock
    with _captured_messages() as messages:
        run = _drive(root, resolver=runner, config=config, clock=clock)
    assert run.error is None, f"the default gitlink policy must not crash: {run.error}"
    reviews = [review for review in (_gitlink_review(message) for message in messages) if review]
    assert len(reviews) == 1, f"one gitlink review must be recorded for later review: {reviews}"
    review = reviews[0]
    assert review.path == _GITLINK_PATH
    assert review.policy == GitlinkPreference.VENDOR_IMMUTABLE.value
    assert (review.base, review.ours, review.theirs) == stage_shas, (
        "the review must record all three stage SHAs"
    )
    assert review.selected in stage_shas, "the review must record the chosen pointer"
    assert review.selected_side in {"ours", "theirs"}
    assert review.alternatives == _DEFAULT_ALTERNATIVES, (
        "the review must record the alternatives the default policy considered"
    )
    assert _gitlink_pointer(root, "main") == review.selected, (
        "the pointer chosen by the default policy must be the one that landed"
    )
    assert_content_equivalence(
        root,
        contract="the default gitlink policy must land the branch",
        feature=feature_sha,
        source_refs=("main",),
    )
    landed_head = _sha(root, "HEAD")
    launches_before = runner.launches
    with _captured_messages() as tick_messages:
        for tick in range(_SUPERVISOR_TICKS):
            clock.advance(1.0)
            tick_run = _drive(root, resolver=runner, config=config, clock=clock)
            assert tick_run.error is None, f"tick {tick} must not crash: {tick_run.error}"
            assert not tick_run.operator_lines, (
                f"tick {tick} must emit nothing further: "
                f"{[line.content for line in tick_run.operator_lines]}"
            )
    assert not [message for message in tick_messages if "conflict_resolution" in message], (
        "the settled branch must not re-enter conflict resolution"
    )
    assert runner.launches == launches_before, "the settled branch must launch no agent"
    assert _sha(root, "HEAD") == landed_head
    assert _gitlink_pointer(root, "main") == review.selected


def test_conflict_kinds_land_without_corruption(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        root = Path(repo.working_tree_dir or tmp_git_repo)
        for relative, payload in (
            ("append.dat", "base\n"),
            ("delete.dat", "base\n"),
            ("binary.dat", "base\n"),
        ):
            (root / relative).write_text(payload, encoding="utf-8")
        repo.index.add(["append.dat", "delete.dat", "binary.dat"])
        repo.index.commit("base")
        repo.git.checkout("-q", "-b", "feature")
        (root / "append.dat").write_text("feature append\n", encoding="utf-8")
        (root / "binary.dat").write_text("\x00feature\x00", encoding="utf-8")
        repo.index.remove(["delete.dat"], working_tree=True)
        repo.index.add(["append.dat", "binary.dat"])
        repo.index.commit("feature changes")
        feature_sha = repo.head.commit.hexsha
        repo.git.checkout("-q", "main")
        (root / "append.dat").write_text("target change\n", encoding="utf-8")
        (root / "delete.dat").write_text("target keeps\n", encoding="utf-8")
        (root / "binary.dat").write_text("target change\n", encoding="utf-8")
        repo.index.add(["append.dat", "delete.dat", "binary.dat"])
        repo.index.commit("target changes")
        target_before = repo.head.commit.hexsha
        repo.git.checkout("-q", "feature")
    finally:
        repo.close()
    run = _drive(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))
    assert run.error is None, f"multi-kind conflict handling must not crash: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="append-only, delete-modify, and binary conflicts must land without losing feature content",
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_empty_after_replay_commit_is_preserved_or_explicitly_excluded(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _write_commit(repo, "shared.txt", "same\n", "base")
        repo.git.checkout("-q", "-b", "feature")
        head = repo.head.commit
        empty_sha = repo.git.commit_tree(head.tree.hexsha, "-p", head.hexsha, "-m", "empty replay")
        repo.git.reset("--hard", empty_sha)
    finally:
        repo.close()
    run = _drive(tmp_git_repo)
    reason = "" if run.state is None else (run.state.last_reason or "")
    preserved = _contains(tmp_git_repo, empty_sha)
    assert preserved or "empty" in reason.lower() or "excluded" in reason.lower(), (
        "an empty-after-replay commit must be preserved or explicitly excluded"
    )


def test_dirty_worktree_content_is_preserved_while_feature_lands(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _write_commit(repo, "base.txt", "base\n", "base")
        repo.git.checkout("-q", "-b", "feature")
        feature_sha = _write_commit(repo, "feature.txt", "feature\n", "feature")
    finally:
        repo.close()
    (tmp_git_repo / "base.txt").write_text("uncommitted\n", encoding="utf-8")
    (tmp_git_repo / "untracked.txt").write_text("uncommitted\n", encoding="utf-8")
    preserved = snapshot_uncommitted(tmp_git_repo, ("base.txt", "untracked.txt"))
    run = _drive(tmp_git_repo)
    assert run.error is None, f"dirty tracked and untracked work must not crash: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="dirty tracked and untracked work must survive while feature content lands",
        feature=feature_sha,
        source_refs=("main",),
        preserved=preserved,
    )


def test_failing_hook_does_not_block_landing(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _write_commit(repo, "base.txt", "base\n", "base")
        repo.git.checkout("-q", "-b", "feature")
        feature_sha = _write_commit(repo, "feature.txt", "feature\n", "feature")
    finally:
        repo.close()
    hook = tmp_git_repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    run = _drive(tmp_git_repo)
    assert run.error is None, f"a failing hook must route to a defined autonomous path: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="a failing hook must not prevent committed feature work from landing",
        feature=feature_sha,
        source_refs=("main",),
    )
