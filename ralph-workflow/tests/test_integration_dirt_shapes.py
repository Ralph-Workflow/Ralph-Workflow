"""Typed dirt-shape classification tests for the ONE worktree classifier.

:func:`ralph.pipeline.integration_observation.classify_worktree` refines
the coarse :class:`WorktreeState` with a closed :class:`DirtShape`
vocabulary: staged/unstaged split, mode-only, intent-to-add, type
change, deleted, renamed/copied, untracked (scratch vs blocking for
landing) and line-ending-only (clean after refresh). The decision table
maps each shape to exactly one deterministic cleanup action; these tests
pin the shape each porcelain/evidence combination proves.

Everything is pure in-memory: porcelain blobs and evidence records, no
Git, no subprocess, no filesystem, no clock. This is the
``audit_test_policy`` contract.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.integration_observation import (
    DirtEvidence,
    DirtShape,
    Reading,
    WorktreeDispatch,
    WorktreeState,
    classify_worktree,
)

pytestmark = pytest.mark.timeout_seconds(10)


# ---------------------------------------------------------------------------
# Porcelain-only shapes: what the blob alone proves.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("porcelain", "expected"),
    (
        ("", DirtShape.NONE),
        (" M src/app.py\n", DirtShape.UNSTAGED_MODIFICATION),
        (" D src/gone.py\n", DirtShape.DELETED),
        (" T src/link.py\n", DirtShape.TYPE_CHANGE),
        ("M  src/app.py\n", DirtShape.STAGED_ONLY),
        ("A  src/new.py\n", DirtShape.STAGED_ONLY),
        ("D  src/gone.py\n", DirtShape.DELETED),
        ("MM src/app.py\n", DirtShape.STAGED_AND_UNSTAGED),
        (" M a.py\n M b.py\n", DirtShape.UNSTAGED_MODIFICATION),
        ("M  a.py\n M b.py\n", DirtShape.STAGED_AND_UNSTAGED),
        (" A notes.txt\n", DirtShape.INTENT_TO_ADD),
        ("?? scratch.txt\n", DirtShape.UNTRACKED_ONLY),
        ("?? scratch.txt\n?? tmp/out.bin\n", DirtShape.UNTRACKED_ONLY),
        ("!! build/output.bin\n", DirtShape.NONE),
        ("UU src/app.py\n", DirtShape.UNMERGED),
        (" M src/app.py\n?? scratch.txt\n", DirtShape.UNSTAGED_MODIFICATION),
    ),
    ids=(
        "clean",
        "unstaged-modification",
        "unstaged-deleted",
        "type-change",
        "staged-modification",
        "staged-add",
        "staged-deleted",
        "staged-and-unstaged",
        "multiple-unstaged",
        "mixed-staged-unstaged",
        "intent-to-add",
        "untracked-only",
        "multiple-untracked",
        "ignored-only",
        "unmerged",
        "tracked-outranks-untracked",
    ),
)
def test_porcelain_maps_to_the_proven_dirt_shape(
    porcelain: str,
    expected: DirtShape,
) -> None:
    """The blob alone proves the least specific shape of its dirt."""
    verdict = classify_worktree(Reading.known(porcelain))

    assert verdict.dirt_shape is expected


def test_staged_rename_maps_to_renamed_or_copied() -> None:
    """A staged rename/copy (the -z two-record form) is its own shape."""
    verdict = classify_worktree(Reading.known("R  old.py\0new.py\0"))

    assert verdict.dirt_shape is DirtShape.RENAMED_OR_COPIED


def test_unmerged_outranks_every_other_shape() -> None:
    """A conflicted index is UNMERGED no matter what else the tree holds."""
    verdict = classify_worktree(Reading.known("UU src/app.py\nM  staged.py\n?? x\n"))

    assert verdict.dirt_shape is DirtShape.UNMERGED


def test_unknown_probe_maps_to_unknown_shape() -> None:
    """An unreadable probe yields the UNKNOWN shape, never a guess."""
    verdict = classify_worktree(Reading.unknown("git status timed out"))

    assert verdict.state is WorktreeState.UNKNOWN
    assert verdict.dirt_shape is DirtShape.UNKNOWN


# ---------------------------------------------------------------------------
# The state arm agrees with the porcelain arm on the shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (WorktreeState.CLEAN, DirtShape.NONE),
        (WorktreeState.TRACKED_CHANGES, DirtShape.UNSTAGED_MODIFICATION),
        (WorktreeState.STAGED, DirtShape.STAGED_ONLY),
        (WorktreeState.UNMERGED, DirtShape.UNMERGED),
        (WorktreeState.UNTRACKED, DirtShape.UNTRACKED_ONLY),
        (WorktreeState.UNKNOWN, DirtShape.UNKNOWN),
    ),
    ids=lambda item: item.value if isinstance(item, WorktreeState) else str(item),
)
def test_state_arm_assigns_the_least_specific_proven_shape(
    state: WorktreeState,
    expected: DirtShape,
) -> None:
    """A bare state refines to the least specific shape it proves."""
    assert classify_worktree(state).dirt_shape is expected


# ---------------------------------------------------------------------------
# Evidence-refined shapes.
# ---------------------------------------------------------------------------


def test_mode_only_evidence_refines_the_shape() -> None:
    """Porcelain shows ``M`` for a chmod; the summary evidence says mode-only."""
    verdict = classify_worktree(
        Reading.known(" M run.sh\n"),
        evidence=DirtEvidence(mode_only_paths=frozenset({"run.sh"})),
    )

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.MODE_ONLY
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_line_ending_only_is_clean_after_refresh() -> None:
    """A dirty verdict with an empty content diff never blocks a gate."""
    verdict = classify_worktree(
        Reading.known(" M src/app.py\n"),
        evidence=DirtEvidence(empty_diff_paths=frozenset({"src/app.py"})),
    )

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.LINE_ENDING_ONLY
    assert verdict.is_clean is True
    assert verdict.dispatch is WorktreeDispatch.ALLOWED
    assert verdict.dispatch_allowed is True


def test_line_ending_evidence_requires_every_dirty_path() -> None:
    """One path with real content keeps the tree blocking."""
    verdict = classify_worktree(
        Reading.known(" M src/app.py\n M src/other.py\n"),
        evidence=DirtEvidence(empty_diff_paths=frozenset({"src/app.py"})),
    )

    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_untracked_blocking_for_landing() -> None:
    """An untracked path incoming target commits would overwrite blocks."""
    verdict = classify_worktree(
        Reading.known("?? src/config.py\n"),
        evidence=DirtEvidence(incoming_overwrite_paths=frozenset({"src/config.py"})),
    )

    assert verdict.state is WorktreeState.UNTRACKED
    assert verdict.dirt_shape is DirtShape.UNTRACKED_BLOCKING
    assert verdict.is_clean is False
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_untracked_scratch_does_not_block_even_with_evidence() -> None:
    """Untracked paths nobody will overwrite stay scratch."""
    verdict = classify_worktree(
        Reading.known("?? notes.txt\n"),
        evidence=DirtEvidence(incoming_overwrite_paths=frozenset({"src/config.py"})),
    )

    assert verdict.dirt_shape is DirtShape.UNTRACKED_ONLY
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


def test_evidence_never_invents_dirt_on_a_clean_tree() -> None:
    """Evidence refines an existing dirty entry; a clean blob stays clean."""
    verdict = classify_worktree(
        Reading.known(""),
        evidence=DirtEvidence(
            mode_only_paths=frozenset({"run.sh"}),
            empty_diff_paths=frozenset({"src/app.py"}),
        ),
    )

    assert verdict.dirt_shape is DirtShape.NONE
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


def test_evidence_defaults_carry_no_refinement() -> None:
    """A default DirtEvidence refines nothing and changes nothing."""
    plain = classify_worktree(Reading.known(" M src/app.py\n"))
    with_empty_evidence = classify_worktree(
        Reading.known(" M src/app.py\n"),
        evidence=DirtEvidence(),
    )

    assert with_empty_evidence == plain


# ---------------------------------------------------------------------------
# Quoting-hostile paths: core.quotePath C-quoted/escaped records.
#
# Line-oriented porcelain (which at least one production probe feeds) quotes
# paths holding non-ASCII bytes, quotes or backslashes; ``-z`` porcelain
# never quotes. Both forms must classify without raising, and the decoded
# path — not the quoted spelling — is what evidence refinements match on.
# ---------------------------------------------------------------------------


def test_quoted_octal_escape_path_classifies_by_xy_code() -> None:
    """A C-quoted non-ASCII path is unstaged dirt, quoted or not."""
    verdict = classify_worktree(Reading.known(' M "caf\\303\\251.py"\n'))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_quoted_path_with_quote_and_backslash_escapes() -> None:
    """Escaped ``\\"`` and ``\\\\`` sequences decode without raising."""
    verdict = classify_worktree(Reading.known(' M "we\\"ird\\\\name.py"\n'))

    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION


def test_mode_only_evidence_matches_a_quoted_porcelain_path() -> None:
    """A chmod on a non-ASCII path refines to MODE_ONLY via the decoded path."""
    verdict = classify_worktree(
        Reading.known(' M "caf\\303\\251.sh"\n'),
        evidence=DirtEvidence(mode_only_paths=frozenset({"café.sh"})),
    )

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.MODE_ONLY
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_mode_only_evidence_matches_a_raw_nul_terminated_non_ascii_path() -> None:
    """``-z`` porcelain carries raw UTF-8; the evidence path matches verbatim."""
    verdict = classify_worktree(
        Reading.known(" M café.sh\0"),
        evidence=DirtEvidence(mode_only_paths=frozenset({"café.sh"})),
    )

    assert verdict.dirt_shape is DirtShape.MODE_ONLY


def test_quoted_path_with_invalid_utf8_bytes_classifies_without_raising() -> None:
    """Octal escapes that are not valid UTF-8 decode via surrogateescape."""
    verdict = classify_worktree(Reading.known(' M "bad\\377\\376.py"\n'))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_quoted_path_with_invalid_utf8_bytes_matches_mode_only_evidence() -> None:
    """The surrogateescape spelling of raw bytes is what evidence paths use."""
    raw_path = "bad\udcff\udcfe.sh"  # surrogateescape spelling of b"bad\xff\xfe.sh"
    verdict = classify_worktree(
        Reading.known(' M "bad\\377\\376.sh"\n'),
        evidence=DirtEvidence(mode_only_paths=frozenset({raw_path})),
    )

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.MODE_ONLY
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_line_ending_evidence_matches_a_quoted_porcelain_path() -> None:
    """An empty content diff on a quoted path is LINE_ENDING_ONLY, not a block."""
    verdict = classify_worktree(
        Reading.known(' M "caf\\303\\251.py"\n'),
        evidence=DirtEvidence(empty_diff_paths=frozenset({"café.py"})),
    )

    assert verdict.dirt_shape is DirtShape.LINE_ENDING_ONLY
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


def test_untracked_overwrite_evidence_matches_a_quoted_untracked_path() -> None:
    """A quoted untracked path incoming commits would overwrite still blocks."""
    verdict = classify_worktree(
        Reading.known('?? "caf\\303\\251/config.py"\n'),
        evidence=DirtEvidence(incoming_overwrite_paths=frozenset({"café/config.py"})),
    )

    assert verdict.state is WorktreeState.UNTRACKED
    assert verdict.dirt_shape is DirtShape.UNTRACKED_BLOCKING
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_quoted_unmerged_path_is_unmerged() -> None:
    """A quoted conflicted path outranks every other shape."""
    verdict = classify_worktree(Reading.known('UU "conf\\303\\251lict.py"\n'))

    assert verdict.state is WorktreeState.UNMERGED
    assert verdict.dirt_shape is DirtShape.UNMERGED


def test_malformed_quoted_path_falls_back_without_raising() -> None:
    """An unterminated quote is not a well-formed C-quote: classify by xy."""
    verdict = classify_worktree(Reading.known(' M "broken.py\n'))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION


# ---------------------------------------------------------------------------
# Submodule (gitlink) dirt records.
#
# Porcelain reports submodule state on the gitlink path with the ordinary
# XY vocabulary (modified content and new commits both render as ``M`` in
# some position), so the classifier must map them like any tracked path.
# ---------------------------------------------------------------------------


def test_submodule_modified_content_is_tracked_dirt() -> None:
    """A dirty submodule gitlink blocks like any tracked modification."""
    verdict = classify_worktree(Reading.known(" M vendor/lib\0"))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_submodule_new_commits_are_staged_dirt() -> None:
    """New submodule commits staged in the index are STAGED_ONLY."""
    verdict = classify_worktree(Reading.known("M  vendor/lib\0"))

    assert verdict.state is WorktreeState.STAGED
    assert verdict.dirt_shape is DirtShape.STAGED_ONLY
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


# ---------------------------------------------------------------------------
# Porcelain v2 (``git status --porcelain=v2 -z``): mode columns prove
# MODE_ONLY from raw porcelain alone, no DirtEvidence involved.
#
# v2 ordinary records are ``1 XY sub mH mI mW hH hI path``: a pure mode
# change keeps the content hashes equal (hH == hI) while a mode column
# differs (staged side mH vs mI, worktree side mI vs mW).
# ---------------------------------------------------------------------------


def test_v2_unstaged_executable_bit_change_is_mode_only_from_raw_porcelain() -> None:
    """A raw ``-z`` v2 record with 100644 -> 100755 proves MODE_ONLY alone."""
    verdict = classify_worktree(
        Reading.known("1 .M N... 100644 100644 100755 e69de29 e69de29 run.sh\0")
    )

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.MODE_ONLY
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_v2_staged_executable_bit_change_is_mode_only_from_raw_porcelain() -> None:
    """A staged chmod (mH != mI, hashes equal) proves MODE_ONLY alone."""
    verdict = classify_worktree(
        Reading.known("1 M. N... 100644 100755 100755 e69de29 e69de29 run.sh\0")
    )

    assert verdict.state is WorktreeState.STAGED
    assert verdict.dirt_shape is DirtShape.MODE_ONLY
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_v2_content_hash_change_is_not_mode_only() -> None:
    """Different HEAD/index content hashes mean real content dirt."""
    verdict = classify_worktree(
        Reading.known("1 M. N... 100644 100755 100755 aaa1111 bbb2222 run.sh\0")
    )

    assert verdict.dirt_shape is DirtShape.STAGED_ONLY
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_v2_mode_only_beside_content_change_is_not_mode_only() -> None:
    """A mode-only path beside a content-dirty path keeps the tree blocking."""
    verdict = classify_worktree(
        Reading.known(
            "1 .M N... 100644 100644 100755 e69de29 e69de29 run.sh\0"
            "1 .M N... 100644 100644 100644 ccc3333 ccc3333 src/app.py\0"
        )
    )

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_v2_untracked_record_maps_to_untracked_only() -> None:
    """A v2 ``?`` record classifies exactly like a v1 ``??`` record."""
    verdict = classify_worktree(Reading.known("? scratch.txt\0"))

    assert verdict.state is WorktreeState.UNTRACKED
    assert verdict.dirt_shape is DirtShape.UNTRACKED_ONLY
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


def test_v2_unmerged_record_is_unmerged() -> None:
    """A v2 ``u`` record outranks every other shape, as the v1 codes do."""
    verdict = classify_worktree(
        Reading.known("u UU N... 100644 100644 100644 aaa1111 bbb2222 ccc3333 src/app.py\0")
    )

    assert verdict.state is WorktreeState.UNMERGED
    assert verdict.dirt_shape is DirtShape.UNMERGED
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_submodule_dirt_outranks_untracked_scratch() -> None:
    """A dirty gitlink beside scratch untracked files stays tracked dirt."""
    verdict = classify_worktree(Reading.known(" M vendor/lib\0?? notes.txt\0"))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION


# ---------------------------------------------------------------------------
# Very large dirt: thousands of porcelain paths must classify in bounded
# time and memory. The manifest is built in memory (no real git, no
# multi-GB tree); the classifier consumes the blob through the same
# pure parse seam every other test here uses, so "bounded" is pinned by
# the suite's per-test timeout plus the closed-vocabulary outcome: the
# thousands of entries collapse to ONE verdict, and a second identical
# classification is byte-equal (no hidden accumulation across calls).
# ---------------------------------------------------------------------------


def test_thousands_of_paths_classify_to_one_bounded_verdict() -> None:
    """AC-10: a very large dirt manifest stays bounded end to end.

    5,000 tracked modifications plus 5,000 untracked scratch paths —
    the scale the criterion's "thousands of paths" names — classify to
    exactly one deterministic verdict: tracked dirt outranks untracked
    scratch, the shape is the single UNSTAGED_MODIFICATION member, and
    repeating the classification yields an identical verdict (the
    classifier is a pure blob -> verdict function, so no per-call state
    can grow with the manifest size).
    """
    entries: list[str] = [f" M src/module_{index}.py\n" for index in range(5_000)]
    entries.extend(f"?? scratch/{index}.bin\n" for index in range(5_000))
    porcelain = "".join(entries)

    verdict = classify_worktree(Reading.known(porcelain))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION
    assert verdict.dispatch is WorktreeDispatch.BLOCKED
    assert verdict.dispatch_allowed is False

    again = classify_worktree(Reading.known(porcelain))
    assert again == verdict, "a re-classification of the same manifest is identical"
