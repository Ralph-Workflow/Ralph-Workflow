"""Outcome judging: in-scope work lands without declare_complete."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.session import invoke_resolution_agent
from ralph.policy.models import PolicyBundle

if TYPE_CHECKING:
    import pytest


def test_invoke_resolution_agent_does_not_require_declare_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[bool] = []

    def _capture_execute(effect: object, *args: object, **kwargs: object) -> object:
        captured.append(bool(getattr(effect, "requires_completion_evidence", True)))
        raise RuntimeError("stop after capturing")

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _capture_execute,
    )
    prompt = Path("/repo/prompt.md")
    invoke_resolution_agent(
        agent_name="claude",
        prompt_path=prompt,
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=PolicyBundle.model_construct(),
        display=None,
        display_context=None,
    )
    assert captured == [False]


def test_identical_blobs_are_mechanical() -> None:
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    kind = classify_stage_map(
        {2: ("100644", "abc"), 3: ("100644", "abc")},
        binary=False,
    )
    assert kind is ConflictSight.MECHANICAL


def test_binary_conflict_is_the_agents_choice_never_ralphs() -> None:
    """Ralph still picks no side -- the resolver does, and must say so.

    The session holds write_file and delete_path, so a binary conflict
    is something it can carry out; what it cannot do is leave evidence
    in the file, since a binary conflict has no markers. So it is a
    declared DECISION. Calling it unreachable escalated the whole
    conflict set on sight, starving every text conflict beside it.
    """
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    kind = classify_stage_map(
        {2: ("100644", "aa"), 3: ("100644", "bb")},
        binary=True,
    )
    assert kind is ConflictSight.AGENT_DECISION


def test_file_directory_collision_is_the_agents_declared_choice() -> None:
    """The session can create directories and delete paths."""
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    kind = classify_stage_map(
        {2: ("100644", "aa"), 3: ("040000", "bb")},
        binary=False,
    )
    assert kind is ConflictSight.AGENT_DECISION


def test_modify_delete_is_the_resolvers_decision_not_out_of_reach() -> None:
    """One side edited, the other deleted: a judgement, not an impossibility.

    Git records stages 1+3 (or 1+2) for a modify/delete and leaves the
    surviving file in the worktree. Escalating it on sight spent no
    resolver on one of the commonest conflicts there is.
    """
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    modified_by_them = {1: ("100644", "base"), 3: ("100644", "theirs")}
    assert classify_stage_map(modified_by_them, binary=False) is ConflictSight.AGENT_DECISION
    modified_by_us = {1: ("100644", "base"), 2: ("100644", "ours")}
    assert classify_stage_map(modified_by_us, binary=False) is ConflictSight.AGENT_DECISION


def test_only_a_submodule_pointer_is_genuinely_out_of_reach() -> None:
    """git is denied to the session, so it cannot write a gitlink.

    Everything else it CAN carry out: the session holds write_file,
    edit_file, delete_path and create_directory. Calling a binary or a
    file-vs-directory collision unreachable escalated the whole conflict
    set on sight, so an ordinary text conflict beside a PNG was never
    offered to any resolver -- on every run, because nothing about the
    repository changed in between.
    """
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    gitlink = {1: ("160000", "base"), 3: ("160000", "theirs")}
    assert classify_stage_map(gitlink, binary=False) is ConflictSight.OUT_OF_REACH
    both_gitlinks = {1: ("160000", "b"), 2: ("160000", "o"), 3: ("160000", "t")}
    assert classify_stage_map(both_gitlinks, binary=False) is ConflictSight.OUT_OF_REACH

    # Carryable, but unreadable off the file: each needs a declaration.
    tree = {1: ("100644", "base"), 2: ("040000", "ours")}
    assert classify_stage_map(tree, binary=False) is ConflictSight.AGENT_DECISION
    assert (
        classify_stage_map({1: ("100644", "b"), 3: ("100644", "t")}, binary=True)
        is ConflictSight.AGENT_DECISION
    )


def test_a_markerless_decision_demands_declared_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ralph cannot SEE a keep-or-delete decision, so it must be declared.

    A modify/delete has no markers, so the textual proof every other
    conflict is judged by is satisfied before anyone touches the file.
    Without declared completion an agent that did nothing would silently
    land "keep", quietly reversing a deletion the other side intended.
    """
    from ralph.pipeline.conflict_resolution import driver as driver_module
    from ralph.pipeline.conflict_resolution.sight import ConflictSight

    captured: list[bool] = []

    def _capture(**kwargs: object) -> bool:
        captured.append(bool(kwargs.get("require_completion_evidence")))
        return False

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["gone.py"])
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, _p: [])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.AGENT_DECISION),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _root, _kinds: ())
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    monkeypatch.setattr(driver_module, "conflict_chain_max_retries", lambda _bundle: 1)
    monkeypatch.setattr(
        driver_module,
        "classify_failed_resolution_attempt",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(driver_module, "invoke_resolution_agent", _capture)
    root = Path("/repo")
    prompt = root / "conflict-prompt.md"
    monkeypatch.setattr(Path, "exists", lambda _path: False)
    monkeypatch.setattr(driver_module, "render_conflict_prompt", lambda **_kwargs: prompt)
    monkeypatch.setattr(Path, "unlink", lambda _path: None)

    driver_module.run_conflict_resolution_pipeline(
        root=root,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=PolicyBundle.model_construct(),
        display=None,
        display_context=None,
    )
    assert captured, "the resolver must be invoked for a markerless decision"
    assert all(captured), "and it must be required to declare its decision"


def test_a_widened_conflict_marker_is_still_a_conflict_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`conflict-marker-size` is a documented gitattribute.

    A repository that widens the fence produced ``<<<<<<<< HEAD``, which
    does not start with ``"<<<<<<< "`` -- and every gate that proves a
    resolution real reads this one scan, so all of them went blind at
    once and conflict markers were committed into history as a success.
    """
    from ralph.git import merge as merge_module

    root = Path("/repo")
    contents = {
        root / "default.txt": "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> main\n",
        root / "wide.txt": "<<<<<<<< HEAD\na\n========\nb\n>>>>>>>> main\n",
        root / "prose.md": "Title\n=======\nbody\n",
        root / "doctest.py": ">>> print(1)\n1\n",
    }
    monkeypatch.setattr(merge_module, "conflict_marker_sizes", lambda _root, _paths: {})
    monkeypatch.setattr(merge_module, "_readable_text", contents.get)

    reported = merge_module.paths_with_conflict_markers(
        root, ["default.txt", "wide.txt", "prose.md", "doctest.py"]
    )
    assert sorted(reported) == ["default.txt", "wide.txt"]


def test_a_markerless_conflict_is_a_decision_however_it_became_markerless() -> None:
    """`binary`, `-merge` and `merge=binary` all suppress the markers.

    A NUL-byte probe cannot see any of them, so an ASCII lockfile was
    judged an ordinary conflict -- and an ordinary conflict passes the
    marker scan the moment it is created, which credited an agent that
    did nothing and dropped the other side.
    """
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    two_sided = {1: ("100644", "base"), 2: ("100644", "ours"), 3: ("100644", "theirs")}
    assert (
        classify_stage_map(two_sided, binary=False, has_markers=True) is ConflictSight.AGENT
    )
    assert (
        classify_stage_map(two_sided, binary=False, has_markers=False)
        is ConflictSight.AGENT_DECISION
    )


def test_a_narrow_conflict_marker_is_still_a_conflict_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`conflict-marker-size` moves the fence in BOTH directions.

    A width the scan does not expect blinds every gate that proves a
    resolution real, so the markers get committed and reported as a
    success. A narrow ``>`` fence is indistinguishable from a Markdown
    blockquote, so the width is asked of git per path rather than
    guessed at.
    """
    from ralph.git import merge as merge_module
    from ralph.git.git_run_result import GitRunResult

    root = Path("/repo")
    paths = ["narrow.txt", "wide.txt", "quote.md"]
    contents = {
        root / "narrow.txt": "<<<< HEAD\na\n====\nb\n>>>> other\n",
        root / "wide.txt": "<<<<<<<< HEAD\na\n========\nb\n>>>>>>>> other\n",
        root / "quote.md": "> a quoted line\n>> nested\n",
    }

    def _fake_run_vcs(
        args: tuple[str, ...], *, cwd: Path, label: str
    ) -> GitRunResult:
        assert args == ("check-attr", "-z", "conflict-marker-size", "--", *paths)
        assert cwd == root
        assert label == "git-conflict-marker-size"
        return GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout="\0".join(("narrow.txt", "conflict-marker-size", "4", "")),
            stderr="",
        )

    monkeypatch.setattr(merge_module, "run_git", _fake_run_vcs)
    monkeypatch.setattr(merge_module, "_readable_text", contents.get)

    reported = merge_module.paths_with_conflict_markers(root, paths)
    assert sorted(reported) == ["narrow.txt", "wide.txt"]

def test_a_non_ascii_path_is_not_reported_as_marker_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NUL-separated git output preserves a non-ASCII marker-bearing path.

    The quoted string cannot be opened, so every content gate that reads
    the path reported "no markers" for a file it never saw -- and the
    markers were committed under a line asserting the stop was clean.
    """
    from ralph.git import merge as merge_module
    from ralph.git.git_run_result import GitRunResult

    named = "é.txt"
    root = Path("/repo")
    git_calls: list[tuple[str, ...]] = []

    def _fake_run_git(
        args: tuple[str, ...], *, cwd: Path, label: str
    ) -> GitRunResult:
        assert cwd == root
        assert label == "git-unmerged-paths"
        git_calls.append(args)
        return GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout=f"{named}\0",
            stderr="",
        )

    def _no_marker_sizes(_root: Path, _paths: list[str]) -> dict[str, int]:
        return {}

    def _marker_content(path: Path) -> str | None:
        if path == root / named:
            return "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> feature\n"
        return None

    monkeypatch.setattr(merge_module, "run_git", _fake_run_git)
    monkeypatch.setattr(merge_module, "conflict_marker_sizes", _no_marker_sizes)
    monkeypatch.setattr(merge_module, "_readable_text", _marker_content)

    unmerged = merge_module.unmerged_paths(root)

    assert git_calls == [("diff", "--name-only", "--diff-filter=U", "-z")]
    assert unmerged == [named], "the path must arrive openable, not git-quoted"
    assert merge_module.paths_with_conflict_markers(root, unmerged) == [named]


def test_a_file_git_wrote_in_utf16_is_not_reported_as_marker_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`working-tree-encoding` makes git write markers we could not read.

    Decoding with ``errors="replace"`` hid the fences behind replacement
    characters, so they were committed and reported as a success.
    """
    from ralph.git import merge as merge_module

    root = Path("/repo")
    contents = {
        root / "u16.txt": "a\n<<<<<<< HEAD\nOURS\n=======\nTHEIRS\n>>>>>>> feature\nc\n",
        root / "clean16.txt": "resolved\n",
    }
    monkeypatch.setattr(merge_module, "conflict_marker_sizes", lambda _root, _paths: {})
    monkeypatch.setattr(merge_module, "_readable_text", contents.get)

    assert merge_module.paths_with_conflict_markers(
        root, ["u16.txt", "clean16.txt"]
    ) == ["u16.txt"]


def test_a_present_but_unreadable_path_is_not_evidence_of_a_clean_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan skipped what it could not read, which passed vacuously."""
    from ralph.git import merge as merge_module

    root = Path("/repo")
    unreadable = root / "locked.txt"
    monkeypatch.setattr(merge_module, "conflict_marker_sizes", lambda _root, _paths: {})
    monkeypatch.setattr(merge_module, "_readable_text", lambda _path: None)
    monkeypatch.setattr(Path, "exists", lambda path: path == unreadable)

    reported = merge_module.paths_with_conflict_markers(root, ["locked.txt", "absent.txt"])
    assert reported == ["locked.txt"], "present but unreadable is not clean"


def test_git_itself_corroborates_the_marker_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Our scan reads the worktree; git reads what would be committed.

    A clean/smudge `filter`, a `working-tree-encoding`, or an unusual
    `conflict-marker-size` all put the committed bytes out of reach of
    an outside reader -- so git's own check is the corroborating gate.
    """
    from ralph.git import merge as merge_module
    from ralph.git.git_run_result import GitRunResult

    root = Path("/repo")

    def _fake_run_git(
        args: tuple[str, ...], *, cwd: Path, label: str
    ) -> GitRunResult:
        assert args == ("diff", "--cached", "--check")
        assert cwd == root
        assert label == "git-staged-marker-check"
        return GitRunResult(
            args=("git", *args),
            returncode=2,
            stdout=(
                "f.txt:2: leftover conflict marker\n"
                "narrow.txt:2: leftover conflict marker\n"
                "f.txt:6: leftover conflict marker\n"
                "clean.txt:1: trailing whitespace.\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(merge_module, "run_git", _fake_run_git)

    reported = merge_module.staged_conflict_marker_paths(root)
    assert sorted(reported) == ["f.txt", "narrow.txt"]


def test_operator_backup_files_are_not_mistaken_for_git_residue() -> None:
    """`f.txt~` is vim's; `f.txt~HEAD` is ort's.

    The residue glob deleted both, destroying operator files that no
    side of the conflict ever mentioned, on every merge resolution and
    every rebase stop.
    """
    from ralph.pipeline.conflict_resolution.ort_residue import is_ort_residue_name

    assert is_ort_residue_name("f.txt", "f.txt~HEAD") is True
    assert is_ort_residue_name("f.txt", "f.txt~feature-branch") is True
    assert is_ort_residue_name("f.txt", "f.txt~") is False
    assert is_ort_residue_name("f.txt", "f.txt~4~") is False
    assert is_ort_residue_name("f.txt", "f.txt~2") is False
