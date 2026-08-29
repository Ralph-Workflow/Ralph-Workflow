"""Outcome judging: in-scope work lands without declare_complete."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.session import invoke_resolution_agent
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest


def test_invoke_resolution_agent_does_not_require_declare_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[bool] = []

    def _capture_execute(effect: object, *args: object, **kwargs: object) -> object:
        captured.append(bool(getattr(effect, "requires_completion_evidence", True)))
        raise RuntimeError("stop after capturing")

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _capture_execute,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    invoke_resolution_agent(
        agent_name="claude",
        prompt_path=prompt,
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=load_policy(
            Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        ),
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    monkeypatch.setattr(driver_module, "invoke_resolution_agent", _capture)

    driver_module.run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=load_policy(
            Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        ),
        display=None,
        display_context=None,
    )
    assert captured, "the resolver must be invoked for a markerless decision"
    assert all(captured), "and it must be required to declare its decision"


def test_a_widened_conflict_marker_is_still_a_conflict_marker(tmp_path: Path) -> None:
    """`conflict-marker-size` is a documented gitattribute.

    A repository that widens the fence produced ``<<<<<<<< HEAD``, which
    does not start with ``"<<<<<<< "`` -- and every gate that proves a
    resolution real reads this one scan, so all of them went blind at
    once and conflict markers were committed into history as a success.
    """
    from ralph.git.merge import paths_with_conflict_markers

    (tmp_path / "default.txt").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> main\n")
    (tmp_path / "wide.txt").write_text("<<<<<<<< HEAD\na\n========\nb\n>>>>>>>> main\n")
    # Still not markers: prose punctuation and a doctest prompt.
    (tmp_path / "prose.md").write_text("Title\n=======\nbody\n")
    (tmp_path / "doctest.py").write_text(">>> print(1)\n1\n")

    reported = paths_with_conflict_markers(
        tmp_path, ["default.txt", "wide.txt", "prose.md", "doctest.py"]
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


def test_a_narrow_conflict_marker_is_still_a_conflict_marker(tmp_path: Path) -> None:
    """`conflict-marker-size` moves the fence in BOTH directions.

    A width the scan does not expect blinds every gate that proves a
    resolution real, so the markers get committed and reported as a
    success. A narrow ``>`` fence is indistinguishable from a Markdown
    blockquote, so the width is asked of git per path rather than
    guessed at.
    """
    from ralph.git.merge import paths_with_conflict_markers
    from ralph.git.subprocess_runner import run_git

    def _git(*args: str) -> None:
        result = run_git(args, cwd=tmp_path, label="test-setup")
        assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"

    _git("init", "-q", ".")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    (tmp_path / ".gitattributes").write_text("narrow.txt conflict-marker-size=4\n")
    (tmp_path / "narrow.txt").write_text("<<<< HEAD\na\n====\nb\n>>>> other\n")
    (tmp_path / "wide.txt").write_text("<<<<<<<< HEAD\na\n========\nb\n>>>>>>>> other\n")
    (tmp_path / "quote.md").write_text("> a quoted line\n>> nested\n")
    _git("add", "-A")

    reported = paths_with_conflict_markers(tmp_path, ["narrow.txt", "wide.txt", "quote.md"])
    assert sorted(reported) == ["narrow.txt", "wide.txt"]


def test_a_non_ascii_path_is_not_reported_as_marker_free(tmp_path: Path) -> None:
    """git QUOTES such a path unless asked for NUL separation.

    The quoted string cannot be opened, so every content gate that reads
    the path reported "no markers" for a file it never saw -- and the
    markers were committed under a line asserting the stop was clean.
    """
    from ralph.git.merge import paths_with_conflict_markers, unmerged_paths
    from ralph.git.subprocess_runner import run_git

    def _git(*args: str) -> None:
        result = run_git(args, cwd=tmp_path, label="test-setup")
        assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"

    _git("init", "-q", ".")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    named = "é.txt"
    (tmp_path / named).write_text("a\n")
    _git("add", "-A")
    _git("commit", "-qm", "base")
    _git("checkout", "-qb", "feature")
    (tmp_path / named).write_text("FEATURE\n")
    _git("commit", "-qam", "feature")
    _git("checkout", "-q", "master")
    (tmp_path / named).write_text("MAIN\n")
    _git("commit", "-qam", "main")
    run_git(("merge", "feature"), cwd=tmp_path, label="test-merge")

    unmerged = unmerged_paths(tmp_path)
    assert unmerged == [named], "the path must arrive openable, not git-quoted"
    assert paths_with_conflict_markers(tmp_path, unmerged) == [named]


def test_a_file_git_wrote_in_utf16_is_not_reported_as_marker_free(tmp_path: Path) -> None:
    """`working-tree-encoding` makes git write markers we could not read.

    Decoding with ``errors="replace"`` hid the fences behind replacement
    characters, so they were committed and reported as a success.
    """
    from ralph.git.merge import paths_with_conflict_markers

    conflicted = "a\n<<<<<<< HEAD\nOURS\n=======\nTHEIRS\n>>>>>>> feature\nc\n"
    (tmp_path / "u16.txt").write_bytes(conflicted.encode("utf-16"))
    (tmp_path / "clean16.txt").write_bytes("resolved\n".encode("utf-16"))

    assert paths_with_conflict_markers(tmp_path, ["u16.txt", "clean16.txt"]) == ["u16.txt"]


def test_a_present_but_unreadable_path_is_not_evidence_of_a_clean_one(
    tmp_path: Path,
) -> None:
    """The scan skipped what it could not read, which passed vacuously."""
    from ralph.git.merge import paths_with_conflict_markers

    unreadable = tmp_path / "locked.txt"
    unreadable.write_text("<<<<<<< HEAD\na\n")
    unreadable.chmod(0o000)
    try:
        reported = paths_with_conflict_markers(tmp_path, ["locked.txt", "absent.txt"])
    finally:
        unreadable.chmod(0o644)
    assert reported == ["locked.txt"], "present but unreadable is not clean"


def test_git_itself_corroborates_the_marker_scan(tmp_path: Path) -> None:
    """Our scan reads the worktree; git reads what would be committed.

    A clean/smudge `filter`, a `working-tree-encoding`, or an unusual
    `conflict-marker-size` all put the committed bytes out of reach of
    an outside reader -- so git's own check is the corroborating gate.
    """
    from ralph.git.merge import staged_conflict_marker_paths
    from ralph.git.subprocess_runner import run_git

    def _git(*args: str) -> None:
        result = run_git(args, cwd=tmp_path, label="test-setup")
        assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"

    _git("init", "-q", ".")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("a\n")
    _git("add", "-A")
    _git("commit", "-qm", "base")

    (tmp_path / "f.txt").write_text("a\n<<<<<<< HEAD\nX\n=======\nY\n>>>>>>> other\n")
    (tmp_path / "narrow.txt").write_text("a\n<<< HEAD\nX\n===\nY\n>>> other\n")
    (tmp_path / ".gitattributes").write_text("narrow.txt conflict-marker-size=3\n")
    (tmp_path / "clean.txt").write_text("resolved\n")
    _git("add", "-A")

    reported = staged_conflict_marker_paths(tmp_path)
    assert sorted(reported) == ["f.txt", "narrow.txt"]


def test_operator_backup_files_are_not_mistaken_for_git_residue() -> None:
    """`f.txt~` is vim's; `f.txt~HEAD` is ort's.

    The residue glob deleted both, destroying operator files that no
    side of the conflict ever mentioned, on every merge resolution and
    every rebase stop.
    """
    from ralph.pipeline.conflict_resolution.rebase_loop import _is_ort_residue_name

    assert _is_ort_residue_name("f.txt", "f.txt~HEAD") is True
    assert _is_ort_residue_name("f.txt", "f.txt~feature-branch") is True
    assert _is_ort_residue_name("f.txt", "f.txt~") is False
    assert _is_ort_residue_name("f.txt", "f.txt~4~") is False
    assert _is_ort_residue_name("f.txt", "f.txt~2") is False
