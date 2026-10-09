"""Black-box tests for the config-write auto-commit contract (wt-012 U1).

Wires the shared ``commit_deterministic_writes`` primitive into the three
config write sites after wt-012 U0:

* ``ralph.config.loader.load_toml`` -- the retired
  ``agent_can_commit`` key migration
  (subject: ``chore(config): migrate retired agent_can_commit assignments``);
* ``ralph.config.agent_detection.autowire_chains_to_detected_agent`` and
  ``enable_detected_agents`` -- the detected-CLI agent-configuration
  updates
  (subject: ``chore(config): update agent configuration``);
* ``ralph.cli.commands.init`` -- the project-local ``PROMPT.md``
  creation on ``ralph --init``
  (subject: ``chore(config): update agent configuration``).

The contract:

* a config write inside a git working tree lands in a deterministic
  chore commit with the pinned subject line above;
* a config write outside a git working tree is the documented
  silent-NOOP branch -- the file is written, no commit is attempted,
  and no error is raised;
* a path the user / agent dirtied BEFORE the deterministic writer
  touched it is SKIPPED with a WARNING log so the chore commit
  cannot sweep their work into a fixed-message commit;
* a FAILED auto-commit (an injected ``create_commit_fn`` that returns
  non-CREATED) restores the pre-staged index byte-for-byte and
  surfaces the failure as an ERROR log without raising.

All tests use real git (``Repo.init`` + per-test ``tmp_path``) and the
production ``create_commit`` / ``stage_files`` from
``ralph.git.operations``, with the explicit exceptions for the FAILED
injection case and the NOOP idempotency check. Tests run in well under
the per-test ``subprocess_e2e`` / ``timeout_seconds(5)`` cap and stay
inside the IMMUTABLE 60 s combined verify budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo
from loguru import logger

from ralph.config.agent_detection import (
    _commit_deterministic_config_write,
    autowire_chains_to_detected_agent,
    enable_detected_agents,
)
from ralph.config.loader import load_toml
from ralph.git.commit_result import CommitCreationResult
from ralph.git.scoped_auto_commit import ScopedCommitResult, ScopedCommitStatus

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(5),
]


# ---------------------------------------------------------------------------
# Shared real-git fixtures
# ---------------------------------------------------------------------------


def _init_repo_with_initial_commit(repo_root: Path) -> None:
    """Create a fresh git repo with one initial empty commit so HEAD exists."""
    repo = Repo.init(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


def _git_log_subjects(repo_root: Path) -> list[str]:
    """Return ``git log`` subject lines (newest first) for the test repo."""
    repo = Repo(repo_root)
    try:
        return [c.message.splitlines()[0] for c in repo.iter_commits()]
    finally:
        repo.close()


def _git_status_clean(repo_root: Path) -> bool:
    """True when the working tree has no tracked-file diff / stage.

    Excludes untracked files: a config write in test 5 may leave an
    untracked ``.agent/ralph-workflow.toml`` when the test deliberately
    does not seed it at HEAD (the documented no-chore-commit contract
    for an unmigrated file). Untracked files are not part of the
    chore-commit contract; only tracked-file diff / stage matters.
    """
    repo = Repo(repo_root)
    try:
        return not repo.is_dirty(untracked_files=False)
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# 1. Migration commits with the fixed subject
# ---------------------------------------------------------------------------


def test_load_toml_migration_lands_in_deterministic_chore_commit(
    tmp_path: Path,
) -> None:
    """A TOML with a retired ``agent_can_commit`` key migrates via a
    fixed-subject chore commit and leaves the tree clean.

    The pre-write hash of the config is captured BEFORE the migration
    runs, the migration rewrites the file, and
    ``commit_deterministic_writes`` produces a chore commit with the
    literal subject
    ``chore(config): migrate retired agent_can_commit assignments``.

    The config file is committed at HEAD before ``load_toml`` runs so
    the deterministic writer's pre-write hash matches HEAD -- the
    retired-key migration is the deterministic writer's own diff, not
    someone else's pre-staged work.
    """
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\ndisplay_name = "Claude Code"\n',
        encoding="utf-8",
    )
    # Seed the retired-key version at HEAD so the pre-write hash matches
    # HEAD before ``load_toml`` runs. Without this, the file would be
    # untracked at HEAD and the deterministic writer would treat it as
    # "already dirty" (HEAD != pre-write) and SKIP the chore commit.
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit("seed retired key", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    data = load_toml(config_path)

    # 1. The migration removed the retired key in-memory.
    assert "can_commit" not in data.get("agents", {}).get("claude", {})
    # 2. The on-disk file also had the retired key stripped.
    rewritten = config_path.read_text(encoding="utf-8")
    assert "can_commit = true" not in rewritten
    assert "[agents.claude]" in rewritten
    # 3. A chore commit with the pinned subject landed.
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): migrate retired agent_can_commit assignments"
    # 4. The post-commit tree is clean -- no untracked / unstaged / staged residue.
    assert _git_status_clean(tmp_path), "tree must be clean after the deterministic commit"


def test_load_toml_migration_commits_via_symlinked_config_path(
    tmp_path: Path,
) -> None:
    """DA-001/DA-008/DA-011: a symlinked config entry commits the LEXICAL path.

    The pre-fix bug: ``_commit_deterministic_config_write`` resolved the
    symlink to capture and commit ``target.toml`` (the symlink target),
    but ``atomic_write_text_if_changed`` writes to the lexical
    ``config.toml`` and ``Path.replace``s the symlink with a regular
    file -- the symlink target is left unchanged (and may end up
    dangling). The pre-write hash of ``target.toml`` then equals its
    on-disk hash after the write, so the deterministic helper
    incorrectly returned ``NOOP`` and the lexical ``config.toml`` entry
    stayed dirty. The fix captures the LEXICAL path so the on-disk
    hash check observes the new regular file at the lexical path.
    """
    _init_repo_with_initial_commit(tmp_path)
    # ``target.toml`` holds the original (un-migrated) content. It is
    # tracked at HEAD so the deterministic writer's pre-write hash
    # matches HEAD before ``load_toml`` runs.
    target = tmp_path / "target.toml"
    target.write_text(
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\ndisplay_name = "Claude Code"\n',
        encoding="utf-8",
    )
    # ``config.toml`` is a tracked symlink to ``target.toml``. The
    # migration helper writes to ``config.toml`` (the lexical entry the
    # user-facing config lives at), so the chore commit must reference
    # ``config.toml`` and leave the replaced lexical entry clean.
    config_link = tmp_path / "config.toml"
    config_link.symlink_to(target)
    repo = Repo(tmp_path)
    try:
        repo.index.add(["target.toml"])
        repo.index.commit("seed target", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        # Add the symlink as a tracked entry AFTER seeding the target
        # so git records the symlink blob (the target text) at HEAD.
        repo.index.add(["config.toml"])
        repo.index.commit("seed symlink", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    # Sanity: ``config.toml`` was tracked as a symlink at HEAD.
    assert config_link.is_symlink(), "config.toml must be a symlink at HEAD"
    tracked_mode = repo_head_tree_mode(tmp_path, "config.toml")
    assert tracked_mode == 0o120000, (
        f"config.toml must be tracked as a symlink at HEAD; mode={tracked_mode!r}"
    )

    data = load_toml(config_link)

    # 1. The migration removed the retired key in-memory.
    assert "can_commit" not in data.get("agents", {}).get("claude", {})
    # 2. The lexical ``config.toml`` is now a regular file (the symlink
    #    was replaced by the atomic temp+rename write).
    assert not config_link.is_symlink(), (
        "atomic_write_text_if_changed must replace the symlink with a regular file"
    )
    rewritten = config_link.read_text(encoding="utf-8")
    assert "can_commit = true" not in rewritten
    assert "[agents.claude]" in rewritten
    # 3. A chore commit with the pinned subject landed on the lexical
    #    ``config.toml`` entry -- not on the (unchanged) symlink target.
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): migrate retired agent_can_commit assignments"
    committed_paths = _head_commit_paths(tmp_path)
    assert "config.toml" in committed_paths, (
        f"chore commit must include the lexical config.toml entry; got: {sorted(committed_paths)}"
    )
    # 4. The post-commit tree is clean: the lexical config.toml entry is
    #    committed, the unchanged symlink target is NOT swept in.
    assert _git_status_clean(tmp_path), (
        "tree must be clean after the deterministic commit; the lexical "
        "config.toml must NOT be left dirty"
    )


def repo_head_tree_mode(repo_root: Path, path: str) -> str:
    """Return the git mode (``100644`` / ``100755`` / ``120000`` / ``160000``) of ``path`` in HEAD.

    Used by the symlink test to assert the file is tracked as a symlink
    (mode ``120000``) at HEAD before the migration runs.
    """
    from git import Repo as _Repo  # local import keeps the top-level imports narrow

    repo = _Repo(repo_root)
    try:
        try:
            return repo.head.commit.tree[path].mode
        except KeyError:
            return ""
    finally:
        repo.close()


def _head_commit_paths(repo_root: Path) -> set[str]:
    """Return every blob/symlink path in the HEAD commit tree.

    Walks the tree depth-first and collects the lexical path of every
    non-tree entry. Used by the symlink test to assert the chore
    commit's HEAD tree includes the lexical ``config.toml`` entry.
    """
    from git import Repo as _Repo  # local import keeps the top-level imports narrow

    repo = _Repo(repo_root)
    try:
        paths: set[str] = set()
        stack = [repo.head.commit.tree]
        while stack:
            subtree = stack.pop()
            for entry in subtree:
                if entry.type == "tree":
                    stack.append(entry)
                else:
                    paths.add(entry.path)
        return paths
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# 2. Autowire commits
# ---------------------------------------------------------------------------


def test_autowire_chains_commits_in_repo_with_fixed_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repo-local ``autowire_chains_to_detected_agent`` lands a
    ``chore(config): update agent configuration`` commit.

    The default chains in ``ralph/policy/defaults/ralph-workflow.toml``
    are pristine (no operator customization). All default agents are
    hidden from ``shutil.which`` so the autowire condition fires; a
    ``detected=["codex"]`` argument pins the rewrite target so the
    test does not depend on the host's installed agent set.
    """
    _init_repo_with_initial_commit(tmp_path)
    defaults = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    main_config = tmp_path / "ralph-workflow.toml"
    main_config.write_text(
        (defaults / "ralph-workflow.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Pre-stash the file at HEAD so the deterministic writer treats
    # the post-write content as a real change.
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit("seed main config", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    monkeypatch.setattr("ralph.config.agent_detection.shutil.which", lambda _: None)

    result = autowire_chains_to_detected_agent(main_config, detected=["codex"])

    assert sorted(result or []) == ["claude"], (
        f"autowire must report the replaced default agents; got: {result!r}"
    )
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): update agent configuration", (
        f"autowire must commit with the fixed subject; got: {subjects[0]!r}"
    )
    assert _git_status_clean(tmp_path), (
        "autowire commit must leave the tree clean for the chore commit"
    )


# ---------------------------------------------------------------------------
# 3. enable_detected_agents commits
# ---------------------------------------------------------------------------


def test_enable_detected_agents_commits_in_repo_with_fixed_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``enable_detected_agents`` activates a detected agent block and
    lands a ``chore(config): update agent configuration`` commit.

    Setup: a project-local ``ralph-workflow-agents.toml`` with an
    untouched ``# @AGENT-BLOCK-START: codex`` / ``# @AGENT-BLOCK-END``
    block. ``detect_installed_agents`` is monkeypatched to return
    ``["codex"]`` so the activation fires deterministically.
    """
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow-agents.toml"
    config_path.write_text(
        "[agents.claude]\n"
        'cmd = "claude"\n'
        "\n"
        "# @AGENT-BLOCK-START: codex\n"
        "# [agents.codex]\n"
        '# cmd = "codex exec"\n'
        "# @AGENT-BLOCK-END\n",
        encoding="utf-8",
    )
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow-agents.toml"])
        repo.index.commit(
            "seed agents config", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
        )
    finally:
        repo.close()

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    enabled = enable_detected_agents(config_path)

    assert enabled == ["codex"]
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): update agent configuration", (
        f"enable must commit with the fixed subject; got: {subjects[0]!r}"
    )
    assert _git_status_clean(tmp_path)
    # The committed content must contain the activated block (uncommented).
    repo = Repo(tmp_path)
    try:
        committed_text = (
            repo.head.commit.tree["ralph-workflow-agents.toml"].data_stream.read().decode()
        )
    finally:
        repo.close()
    assert "[agents.codex]" in committed_text
    assert "# @AGENT-BLOCK-START" not in committed_text


# ---------------------------------------------------------------------------
# 3b. enable_detected_agents through a symlinked config path (wt-012 DA-001)
# ---------------------------------------------------------------------------


def test_enable_detected_agents_commits_via_symlinked_config_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wt-012 DA-001/DA-007/DA-008/DA-011: ``write_text_if_changed``
    FOLLOWS the symlink and rewrites ``target.toml``; the chore commit
    must capture the resolved target, not leave it dirty.

    The pre-fix helper captured only the lexical ``config.toml`` path.
    The write went through the symlink to ``target.toml``, the lexical
    path was byte-unchanged (still the same symlink), and the dirty
    ``target.toml`` was left uncommitted in the working tree -- the
    exact ``HEAD_UNCHANGED True`` / ``STATUS ' M target.toml'``
    counterexample from the analysis. The fix captures BOTH the
    lexical path and the resolved target so the deterministic commit
    stages whichever entry actually changed.
    """
    _init_repo_with_initial_commit(tmp_path)
    target = tmp_path / "target.toml"
    target.write_text(
        "[agents.claude]\n"
        'cmd = "claude"\n'
        "\n"
        "# @AGENT-BLOCK-START: codex\n"
        "# [agents.codex]\n"
        '# cmd = "codex exec"\n'
        "# @AGENT-BLOCK-END\n",
        encoding="utf-8",
    )
    config_link = tmp_path / "config.toml"
    config_link.symlink_to(target)
    repo = Repo(tmp_path)
    try:
        repo.index.add(["target.toml"])
        repo.index.commit("seed target", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        repo.index.add(["config.toml"])
        repo.index.commit("seed symlink", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    enabled = enable_detected_agents(config_link)

    assert enabled == ["codex"]
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): update agent configuration", (
        f"enable must commit with the fixed subject; got: {subjects[0]!r}"
    )
    # The lexical entry is still a symlink (the write went THROUGH it)
    # and the tree is clean -- the resolved target's update is
    # committed, not left as ' M target.toml'.
    assert config_link.is_symlink(), (
        "write_text_if_changed must follow the symlink, leaving the "
        "lexical config.toml entry a symlink"
    )
    assert _git_status_clean(tmp_path), (
        "tree must be clean after the deterministic commit; the resolved "
        "target.toml update must NOT be left dirty"
    )
    repo = Repo(tmp_path)
    try:
        committed_text = repo.head.commit.tree["target.toml"].data_stream.read().decode()
    finally:
        repo.close()
    assert "[agents.codex]" in committed_text, (
        "the committed target.toml must contain the activated agent block"
    )
    assert "# @AGENT-BLOCK-START" not in committed_text


# ---------------------------------------------------------------------------
# 4. Global config path -> no commit, no repo created
# ---------------------------------------------------------------------------


def test_global_config_path_writes_file_but_creates_no_commit(
    tmp_path: Path,
) -> None:
    """A config write under a path with no ``.git`` ancestor lands the
    file on disk but does NOT create a repo and does NOT raise.

    Uses the helper directly with a ``write_fn`` that writes a fresh
    TOML under ``tmp_path`` (which has no ``.git`` ancestor). Asserts:

    * no ``.git`` directory is created (the helper MUST NOT init a repo);
    * the file is written (the silent-NOOP branch still persists the
      config the operator requested);
    * the helper returns ``None`` (the documented NOT_REPO contract);
    * no commit is attempted.
    """
    config_path = tmp_path / "subdir" / "no-repo" / "ralph-workflow.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel: dict[str, object] = {"called": False}

    def write_fn() -> object:
        sentinel["called"] = True
        config_path.write_text("[general]\nverbosity = 1\n", encoding="utf-8")
        return None

    result = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=write_fn,
    )

    assert sentinel["called"] is True, "write_fn MUST run even on the NOT_REPO branch"
    assert result is None, f"NOT_REPO branch must return None; got: {result!r}"
    assert config_path.exists(), "config file must land on disk"
    assert not (tmp_path / ".git").exists(), (
        "the helper MUST NOT create a git repo on the NOT_REPO branch"
    )


# ---------------------------------------------------------------------------
# 5. Unmigrated file untouched (no commit, no chore)
# ---------------------------------------------------------------------------


def test_load_toml_unmigrated_file_produces_no_commit(
    tmp_path: Path,
) -> None:
    """A TOML with no retired ``can_commit`` key MUST NOT trigger a commit.

    The migration helper is a no-op when the source has no retired
    assignments, so ``commit_deterministic_writes`` is not even called
    -- HEAD remains the initial commit and no chore commit lands.
    """
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "[general]\nverbosity = 1\n",
        encoding="utf-8",
    )

    data = load_toml(config_path)

    assert data == {"general": {"verbosity": 1}}
    subjects = _git_log_subjects(tmp_path)
    assert subjects == ["initial"], (
        f"unmigrated file must NOT trigger a chore commit; got: {subjects!r}"
    )
    assert _git_status_clean(tmp_path)


# ---------------------------------------------------------------------------
# 6. Unrelated dirty / staged file preserved
# ---------------------------------------------------------------------------


def test_unrelated_prestaged_file_is_preserved_across_config_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-staged unrelated work MUST survive a config chore commit.

    The user pre-stages ``src/main.py`` BEFORE the config writer runs.
    The ``chore(config): migrate retired agent_can_commit assignments``
    commit must:

    * include the migrated config file;
    * NOT include ``src/main.py`` (pre-staged work is the agent's
      responsibility, not the deterministic writer's);
    * leave ``src/main.py`` staged after the chore commit (the
      snapshot-and-restore contract).
    """
    _init_repo_with_initial_commit(tmp_path)
    # Seed the unrelated file at HEAD.
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "main.py"
    src_file.write_text("print('v1')\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["src/main.py"])
        repo.index.commit("seed src", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    # Seed the config file at HEAD so the deterministic writer's
    # pre-write hash matches HEAD before the migration runs.
    config_path = tmp_path / ".agent" / "ralph-workflow.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\n',
        encoding="utf-8",
    )
    repo = Repo(tmp_path)
    try:
        repo.index.add([".agent/ralph-workflow.toml"])
        repo.index.commit(
            "seed config with retired key",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()

    # User pre-stages a mid-work edit on src/main.py AFTER all the
    # seed commits -- the index is clean before the user stages, and
    # the pre-staged state is the only thing tracking the in-progress
    # edit when ``load_toml`` runs.
    src_file.write_text("print('v2')\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["src/main.py"])
        staged_before = sorted(repo.git.diff("--cached", "--name-only").splitlines())
        assert staged_before == ["src/main.py"], (
            "Setup invariant: src/main.py MUST be pre-staged before the auto-commit"
        )
    finally:
        repo.close()

    load_toml(config_path)

    # Chore commit landed with the fixed subject.
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): migrate retired agent_can_commit assignments"

    # The chore commit MUST NOT include the pre-staged src/main.py.
    repo = Repo(tmp_path)
    try:
        head_commit = repo.head.commit
        committed_paths = {diff.a_path for diff in head_commit.diff(head_commit.parents[0])}
        assert ".agent/ralph-workflow.toml" in committed_paths
        assert "src/main.py" not in committed_paths
        # The user's pre-staged src/main.py is still staged after the
        # chore commit -- snapshot-and-restore contract preserved.
        staged_after = sorted(repo.git.diff("--cached", "--name-only").splitlines())
        assert staged_after == ["src/main.py"]
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# 7. FAILED injection -- stub create_commit_fn returns non-CREATED
# ---------------------------------------------------------------------------


def test_failed_create_commit_injection_preserves_head_and_index(
    tmp_path: Path,
) -> None:
    """An injected failing ``create_commit_fn`` produces a FAILED result,
    surfaces an ERROR log, and leaves HEAD + the pre-staged index intact.

    The deterministic commit must NEVER raise on a FAILED
    ``create_commit`` -- a broken git state must not block the
    pipeline; the failure is visible to the operator via the log line.
    """
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\n',
        encoding="utf-8",
    )
    # Seed the config at HEAD so the deterministic writer's pre-write
    # hash matches HEAD before the failing commit runs. Without this,
    # the path would be untracked at HEAD and the helper would SKIP
    # the chore commit (the "already dirty" pre-condition), defeating
    # the FAILED-injection scenario the test pins.
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed config with retired key",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()
    # Pre-stage an unrelated file so the snapshot-and-restore contract
    # is exercised on the FAILED rollback path.
    src_file = tmp_path / "src" / "main.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("print('wip')\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["src/main.py"])
        repo.index.commit("seed src", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        # Re-stage the wip edit to simulate a user mid-work state.
        src_file.write_text("print('wip v2')\n", encoding="utf-8")
        repo.index.add(["src/main.py"])
    finally:
        repo.close()

    head_before = Repo(tmp_path).head.commit.hexsha

    def _failing_create_commit(
        _repo_root: Path, _message: str, *, expected_head: str
    ) -> CommitCreationResult:
        del expected_head
        return CommitCreationResult.failed("simulated commit failure")

    # Capture loguru ERROR messages via a temporary sink; the standard
    # ``caplog`` fixture does not intercept loguru output.
    error_records: list[str] = []
    sink_id = logger.add(error_records.append, level="ERROR", format="{message}")
    try:
        result = _commit_deterministic_config_write(
            config_path,
            subject="chore(config): migrate retired agent_can_commit assignments",
            write_fn=lambda: config_path.write_text(
                '[agents.claude]\ncmd = "claude"\n',
                encoding="utf-8",
            ),
            create_commit_fn=_failing_create_commit,
        )
    finally:
        logger.remove(sink_id)

    assert result is not None
    assert result.status is ScopedCommitStatus.FAILED, (
        f"injected failure must report FAILED; got: {result!r}"
    )

    # HEAD is unchanged -- the failed attempt did not advance HEAD.
    assert Repo(tmp_path).head.commit.hexsha == head_before
    # The pre-staged src/main.py edit is preserved across the rollback.
    repo = Repo(tmp_path)
    try:
        staged_after = sorted(repo.git.diff("--cached", "--name-only").splitlines())
        assert staged_after == ["src/main.py"], (
            "pre-staged src/main.py MUST be restored after the FAILED rollback"
        )
        # The file is on disk (the write_fn ran before the commit attempt)
        # but the migration commit is not in HEAD.
        committed_paths = {
            diff.a_path for diff in repo.head.commit.diff(repo.head.commit.parents[0])
        }
        assert "ralph-workflow.toml" not in committed_paths
    finally:
        repo.close()
    # The ERROR log was emitted.
    assert any("FAILED" in line for line in error_records), (
        f"FAILED status must surface as an ERROR log; got: {error_records!r}"
    )


# ---------------------------------------------------------------------------
# 8. Repeat run is a no-op (idempotent)
# ---------------------------------------------------------------------------


def test_repeat_config_write_is_a_noop(tmp_path: Path) -> None:
    """A second identical migration write produces no commit.

    After the first commit, the on-disk content of the config matches
    HEAD's blob for the same path. A second ``load_toml`` call sees no
    diff (``migrated_source == source``) and ``commit_deterministic_writes``
    correctly reports NOOP -- the chore commit does NOT land a second
    time on an idempotent re-run.
    """
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\n',
        encoding="utf-8",
    )
    # Seed the retired-key version at HEAD so the deterministic writer's
    # pre-write hash matches HEAD before ``load_toml`` runs.
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed config with retired key",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()

    # First call -- the migration commit lands.
    load_toml(config_path)
    head_after_first = Repo(tmp_path).head.commit.hexsha
    subjects_after_first = _git_log_subjects(tmp_path)
    assert subjects_after_first[0] == "chore(config): migrate retired agent_can_commit assignments"

    # Second call -- the on-disk file no longer has the retired key, so
    # the migration is a no-op and no chore commit lands.
    load_toml(config_path)
    head_after_second = Repo(tmp_path).head.commit.hexsha
    assert head_after_second == head_after_first, (
        "second identical write MUST NOT produce a new chore commit"
    )
    assert _git_status_clean(tmp_path)


# ---------------------------------------------------------------------------
# Helper-level direct checks: write_fn always runs (NOT_REPO branch)
# ---------------------------------------------------------------------------


def test_helper_keeps_write_fn_pure_even_when_commit_doesnt_apply(
    tmp_path: Path,
) -> None:
    """The write_fn side effect MUST happen even on the NOT_REPO branch.

    Documents the contract for callers: the helper is a thin
    coordination layer, not a write gate. A non-repo write_fn still
    mutates the file, and the helper just skips the commit attempt.
    """
    config_path = tmp_path / "fresh" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    writes: list[int] = []

    def counting_write_fn() -> object:
        writes.append(1)
        config_path.write_text("[general]\n", encoding="utf-8")
        return None

    result = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=counting_write_fn,
    )
    assert result is None
    assert writes == [1]
    assert config_path.exists()


# ---------------------------------------------------------------------------
# Helper-level direct check: FAILED result is observable through the helper
# ---------------------------------------------------------------------------


def test_helper_returns_scoped_commit_result_on_cre_commit_outcome(
    tmp_path: Path,
) -> None:
    """Successful commit path returns a CREATED ``ScopedCommitResult``.

    Pins the helper's return-type contract for the production path:
    callers that need the commit SHA can read ``result.sha``. The
    helper's own logging surfaces the result regardless.
    """
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "new = true\n",
        encoding="utf-8",
    )
    # Seed the initial content at HEAD so the deterministic writer's
    # pre-write hash matches HEAD before the helper's write_fn runs.
    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed config",
            author=Actor("t", "t@t"),
            committer=Actor("t", "t@t"),
        )
    finally:
        repo.close()

    result = _commit_deterministic_config_write(
        config_path,
        subject="chore(config): update agent configuration",
        write_fn=lambda: config_path.write_text("new = true\n# updated\n", encoding="utf-8"),
    )
    assert isinstance(result, ScopedCommitResult)
    assert result.status is ScopedCommitStatus.CREATED
    assert result.sha is not None
    assert Repo(tmp_path).head.commit.hexsha == result.sha


# ---------------------------------------------------------------------------
# Helper-level direct check: loguru logger side effect (the DEBUG message
# names the path that was committed).
# ---------------------------------------------------------------------------


def test_helper_logs_deterministic_subject_at_debug_on_created(
    tmp_path: Path,
) -> None:
    """The deterministic chore commit lands a DEBUG log line that names
    the config path so an operator can attribute the auto-commit to
    the right call site.
    """
    _init_repo_with_initial_commit(tmp_path)
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "v = 1\n",
        encoding="utf-8",
    )

    debug_records: list[str] = []
    sink_id = logger.add(debug_records.append, level="DEBUG", format="{message}")
    try:
        _commit_deterministic_config_write(
            config_path,
            subject="chore(config): update agent configuration",
            write_fn=lambda: config_path.write_text("v = 2\n", encoding="utf-8"),
        )
    finally:
        logger.remove(sink_id)

    assert any(str(config_path) in line for line in debug_records), (
        f"DEBUG log must name the committed path; got: {debug_records!r}"
    )
