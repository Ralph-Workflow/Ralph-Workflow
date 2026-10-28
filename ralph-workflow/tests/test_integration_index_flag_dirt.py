"""WU-2: the typed index-flag probe that closes the hidden-dirt blind spot.

``git update-index --assume-unchanged`` and ``git update-index
--skip-worktree`` hide a path from ``git status --porcelain``, from
``git diff HEAD`` and from ``git stash create`` (all verified against
real git in ``tests/test_integration_dirt_edge_cases.py``), so a
flagged-only dirty tree used to classify as CLEAN and its content was
never preserved. :func:`~ralph.pipeline.integration_observation.read_index_flag_dirt`
is the bounded, typed probe that reads the flags (``git ls-files -v -z``)
and the real content (``git hash-object`` vs ``git ls-tree HEAD``); its
reading feeds :func:`~ralph.pipeline.integration_observation.classify_worktree`
so hidden dirt types as ``DirtShape.INDEX_FLAG_HIDDEN`` — blocking,
never clean — and an unreadable probe is UNKNOWN: back off and
re-observe, never certify clean.

Everything here is fake-based: ``run_git`` is monkeypatched with a
scripted stand-in (no real git, no subprocess, no sleeps), and the
classifier/decision-table checks are pure. This is the
``audit_test_policy`` contract.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import integration_observation as obs
from ralph.pipeline.integration_cleanup import (
    PlanningBaseline,
    detect_planning_residue,
)
from ralph.pipeline.integration_decision import (
    AttemptState,
    DecisionContext,
    DirtAttribution,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    IndexFlagDirt,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
    classify_worktree,
    read_index_flag_dirt,
    read_worktree,
)

pytestmark = pytest.mark.timeout_seconds(10)

_ROOT = Path("/fake/root")

#: Worktree content hash standing in for ``git hash-object`` output; the
#: HEAD blob oid for the same content (deterministic, parse-safe).
_CONTENT = "edited under the flag"
_WT_HASH = hashlib.sha256(_CONTENT.encode()).hexdigest()
_HEAD_OID = hashlib.sha256(b"base").hexdigest()
_OTHER_WT_HASH = hashlib.sha256(b"other edit").hexdigest()


class _ScriptedGit:
    """``run_git`` stand-in answering per subcommand from a script."""

    def __init__(
        self,
        *,
        ls_files: str = "",
        ls_files_rc: int = 0,
        hash_object: str = "",
        hash_object_rc: int = 0,
        ls_tree: str = "",
        ls_tree_rc: int = 0,
        status: str = "",
        status_rc: int = 0,
    ) -> None:
        self._ls_files = ls_files
        self._ls_files_rc = ls_files_rc
        self._hash_object = hash_object
        self._hash_object_rc = hash_object_rc
        self._ls_tree = ls_tree
        self._ls_tree_rc = ls_tree_rc
        self._status = status
        self._status_rc = status_rc
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        label: str,
        options: object | None = None,
    ) -> GitRunResult:
        """Dispatch one scripted answer by subcommand; assert probe shape."""
        assert cwd is not None
        assert label
        argv = tuple(args)
        self.calls.append(argv)
        # Find the subcommand after any leading ``-c key=value`` pins.
        offset = 0
        while offset < len(argv) and argv[offset] == "-c":
            offset += 2
        config_tail = argv[offset:]
        if config_tail[:3] == ("ls-files", "-v", "-z"):
            return GitRunResult(
                args=argv, returncode=self._ls_files_rc, stdout=self._ls_files, stderr=""
            )
        if config_tail[:1] == ("hash-object",):
            assert config_tail[1] == "--"
            return GitRunResult(
                args=argv,
                returncode=self._hash_object_rc,
                stdout=self._hash_object,
                stderr="" if self._hash_object_rc == 0 else "fatal: could not open",
            )
        if config_tail[:2] == ("ls-tree", "-z"):
            return GitRunResult(
                args=argv, returncode=self._ls_tree_rc, stdout=self._ls_tree, stderr=""
            )
        if config_tail[:3] == ("status", "--porcelain", "-z"):
            return GitRunResult(
                args=argv, returncode=self._status_rc, stdout=self._status, stderr=""
            )
        raise AssertionError(f"unexpected git invocation: {argv}")


_FLAGGED_LS_FILES = "h src/app.py\0S docs/notes.md\0H src/clean.py\0"


def _install(
    monkeypatch: pytest.MonkeyPatch,
    fake: _ScriptedGit,
) -> None:
    monkeypatch.setattr(obs, "run_git", fake.run)


# ---------------------------------------------------------------------------
# The probe itself.
# ---------------------------------------------------------------------------


def test_probe_reads_both_flag_kinds_and_detects_changed_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ls-files -v`` names the flags; content hashes find the hidden edit."""
    fake = _ScriptedGit(
        ls_files=_FLAGGED_LS_FILES,
        # hash-object answers in argv path order (sorted): notes.md
        # matches HEAD; app.py content differs from HEAD.
        hash_object=f"{_HEAD_OID}\n{_WT_HASH}\n",
        ls_tree="\0".join(
            (
                f"100644 blob {_HEAD_OID}\tsrc/app.py",
                f"100644 blob {_HEAD_OID}\tdocs/notes.md",
            )
        )
        + "\0",
    )
    _install(monkeypatch, fake)

    reading = read_index_flag_dirt(_ROOT, git_runner=fake.run)

    assert reading.is_known_good
    dirt = reading.require_known()
    assert dirt.assume_unchanged == frozenset({"src/app.py"})
    assert dirt.skip_worktree == frozenset({"docs/notes.md"})
    assert dirt.changed_paths == frozenset({"src/app.py"})
    assert dirt.path_hashes == (
        ("docs/notes.md", _HEAD_OID),
        ("src/app.py", _WT_HASH),
    )
    # Bounded: three git calls total, one per probe stage.
    assert len(fake.calls) == 3


def test_probe_short_circuits_when_nothing_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag-free tree costs one git call and answers empty — never unknown."""
    fake = _ScriptedGit(ls_files="H src/clean.py\0")
    _install(monkeypatch, fake)

    reading = read_index_flag_dirt(_ROOT, git_runner=fake.run)

    assert reading.is_known_good
    assert reading.require_known() == IndexFlagDirt.empty()
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "script",
    [
        _ScriptedGit(ls_files_rc=128),
        _ScriptedGit(ls_files="h src/app.py\0", hash_object_rc=128),
        _ScriptedGit(ls_files="h src/app.py\0", ls_tree_rc=128),
    ],
    ids=["ls-files-unreadable", "hash-object-unreadable", "ls-tree-unreadable"],
)
def test_probe_failure_is_unknown_never_clean(
    monkeypatch: pytest.MonkeyPatch,
    script: _ScriptedGit,
) -> None:
    """Any unreadable stage backs off: unknown, not an empty (clean) reading."""
    reading = read_index_flag_dirt(_ROOT, git_runner=script.run)

    assert reading.is_unknown
    assert reading.value is None
    assert reading.reason


def test_probe_unknown_content_hash_count_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated hash-object stream proves nothing; fail closed."""
    fake = _ScriptedGit(
        ls_files="h src/app.py\0S docs/notes.md\0",
        hash_object=f"{_WT_HASH}\n",  # one hash for two flagged paths
        ls_tree=f"100644 blob {_HEAD_OID}\tsrc/app.py\0",
    )
    _install(monkeypatch, fake)

    reading = read_index_flag_dirt(_ROOT, git_runner=fake.run)

    assert reading.is_unknown


# ---------------------------------------------------------------------------
# The classifier: hidden dirt is blocking, never clean.
# ---------------------------------------------------------------------------


def _dirt(changed: frozenset[str] = frozenset({"src/app.py"})) -> IndexFlagDirt:
    return IndexFlagDirt(
        assume_unchanged=changed,
        skip_worktree=frozenset(),
        changed_paths=changed,
        path_hashes=tuple((path, _WT_HASH) for path in sorted(changed)),
    )


def test_classifier_flags_hidden_dirt_as_blocking_shape() -> None:
    """Clean porcelain + changed flagged content = BLOCKED, INDEX_FLAG_HIDDEN."""
    verdict = classify_worktree(Reading.known(""), index_flag_dirt=_dirt())

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.INDEX_FLAG_HIDDEN
    assert verdict.dispatch_blocked
    assert not verdict.is_clean
    assert "src/app.py" in verdict.reason


def test_classifier_unmerged_porcelain_outranks_flag_evidence() -> None:
    """A real conflict stays UNMERGED: the operation ladder owns it, not cleanup."""
    verdict = classify_worktree(
        Reading.known("UU src/app.py\0"),
        index_flag_dirt=_dirt(),
    )

    assert verdict.state is WorktreeState.UNMERGED
    assert verdict.dirt_shape is DirtShape.UNMERGED
    assert verdict.dispatch_blocked


def test_classifier_clean_tree_without_flags_is_unchanged() -> None:
    """No flags, clean porcelain: the exact legacy CLEAN verdict."""
    with_flags = classify_worktree(Reading.known(""), index_flag_dirt=IndexFlagDirt.empty())
    without = classify_worktree(Reading.known(""))

    assert without.state is WorktreeState.CLEAN
    assert without.dispatch_allowed
    assert with_flags == without


def test_classifier_visible_dirt_keep_their_shapes_with_flag_evidence() -> None:
    """Flag-free evidence never rewrites an ordinary visible-dirt verdict."""
    assert classify_worktree(
        Reading.known(" M src/app.py\0"), index_flag_dirt=IndexFlagDirt.empty()
    ) == classify_worktree(Reading.known(" M src/app.py\0"))


# ---------------------------------------------------------------------------
# read_worktree: the observation seam runs the probe and fails closed.
# ---------------------------------------------------------------------------


def test_read_worktree_blocks_on_flag_hidden_dirt(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through the seam: hidden dirt is observed as blocking."""
    fake = _ScriptedGit(
        status="",
        ls_files="h src/app.py\0",
        hash_object=f"{_WT_HASH}\n",
        ls_tree=f"100644 blob {_HEAD_OID}\tsrc/app.py\0",
    )
    _install(monkeypatch, fake)

    observed = read_worktree(_ROOT)

    assert observed.is_known_good
    verdict = observed.require_known()
    assert verdict.dirt_shape is DirtShape.INDEX_FLAG_HIDDEN
    assert verdict.dispatch_blocked
    assert "src/app.py" in verdict.reason


def test_read_worktree_unknown_flag_probe_never_certifies_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable flag probe turns a clean-looking tree into UNKNOWN."""
    fake = _ScriptedGit(status="", ls_files_rc=128)
    _install(monkeypatch, fake)

    observed = read_worktree(_ROOT)

    assert observed.is_unknown
    assert observed.value is None
    assert observed.reason


# ---------------------------------------------------------------------------
# The pure residue diff: baseline vs after flag content.
# ---------------------------------------------------------------------------


def _flag_baseline(flag_hashes: tuple[tuple[str, str], ...]) -> PlanningBaseline:
    return PlanningBaseline(
        head_sha="h" * 40,
        status_blob="",
        tracked_diff_hash="d" * 64,
        fingerprint="f" * 64,
        human_work_ref=None,
        flag_hashes=flag_hashes,
    )


def test_detect_residue_flags_content_changed_under_the_flag() -> None:
    """A flagged path whose content moved since baseline is typed residue."""
    baseline = _flag_baseline((("src/app.py", _HEAD_OID),))
    after = IndexFlagDirt(
        assume_unchanged=frozenset({"src/app.py"}),
        skip_worktree=frozenset(),
        changed_paths=frozenset({"src/app.py"}),
        path_hashes=(("src/app.py", _WT_HASH),),
    )

    residue = detect_planning_residue(baseline, "", after_diff_hash="d" * 64, after_flag_dirt=after)

    assert residue is not None
    assert residue.index_flag_paths == ("src/app.py",)
    assert residue.tracked_paths == ()


def test_detect_residue_none_when_flagged_content_unchanged() -> None:
    """Flag set, content identical to baseline: nothing changed, no residue."""
    baseline = _flag_baseline((("src/app.py", _HEAD_OID),))
    after = IndexFlagDirt(
        assume_unchanged=frozenset({"src/app.py"}),
        skip_worktree=frozenset(),
        changed_paths=frozenset(),
        path_hashes=(("src/app.py", _HEAD_OID),),
    )

    assert (
        detect_planning_residue(baseline, "", after_diff_hash="d" * 64, after_flag_dirt=after)
        is None
    )


def test_detect_residue_newly_flagged_path_with_changed_content() -> None:
    """A path flagged during the dispatch (absent at baseline) is residue."""
    baseline = _flag_baseline(())
    after = IndexFlagDirt(
        assume_unchanged=frozenset({"src/app.py"}),
        skip_worktree=frozenset(),
        changed_paths=frozenset({"src/app.py"}),
        path_hashes=(("src/app.py", _OTHER_WT_HASH),),
    )

    residue = detect_planning_residue(baseline, "", after_diff_hash="d" * 64, after_flag_dirt=after)

    assert residue is not None
    assert residue.index_flag_paths == ("src/app.py",)


def test_detect_residue_legacy_callers_without_probe_stay_clean() -> None:
    """A no-flag baseline probed by an old caller (no after reading) stays None."""
    baseline = _flag_baseline(())

    assert detect_planning_residue(baseline, "", after_diff_hash="d" * 64) is None


# ---------------------------------------------------------------------------
# The decision table: the shape climbs the cleanup ladder, never a resolver.
# ---------------------------------------------------------------------------


def _observation() -> Observation:
    return Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )


def test_index_flag_hidden_shape_routes_to_save_and_restore() -> None:
    """Both attributions answer the first cleanup rung — never COMMIT, never resolver."""
    for attribution in (DirtAttribution.UNOWNED, DirtAttribution.UNKNOWN):
        action = decide(
            DecisionContext(
                observation=_observation(),
                attempts=AttemptState(),
                dirt_shape=DirtShape.INDEX_FLAG_HIDDEN,
                dirt_attribution=attribution,
            )
        )
        assert action is NextAction.SAVE_AND_RESTORE


def test_index_flag_hidden_shape_climbs_the_ladder_not_the_resolver(
    tmp_path: Path,
) -> None:
    """Under repeat pressure the shape escalates rung by rung, saturating."""
    from ralph.pipeline.integration_owner import (
        charge_integration_attempt,
        load_integration_attempts,
    )

    observation = _observation()
    ledger_path = tmp_path / ".agent" / "index-flag-ledger.json"
    decided: list[NextAction] = []
    for _ in range(8):
        attempts = load_integration_attempts(tmp_path, ledger_path=ledger_path)
        action = decide(
            DecisionContext(
                observation=observation,
                attempts=attempts,
                dirt_shape=DirtShape.INDEX_FLAG_HIDDEN,
                dirt_attribution=DirtAttribution.UNOWNED,
            )
        )
        decided.append(action)
        charge_integration_attempt(
            tmp_path, observation=observation, next_action=action, ledger_path=ledger_path
        )

    assert decided[0] is NextAction.SAVE_AND_RESTORE
    assert NextAction.DISPATCH_RESOLVER not in decided
    assert len(set(decided)) > 1, "repeat pressure escalates instead of re-running"
