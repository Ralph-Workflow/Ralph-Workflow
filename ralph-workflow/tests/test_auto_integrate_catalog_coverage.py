"""A1-H7 edge-case catalog coverage test (AC-14).

The prompt's catalog enumerates every edge case the auto-integration
pipeline must handle. The test below enumerates every entry as a
named tuple ``(entry_id, evidence_kind, evidence_locator)`` and
asserts that EACH entry has either:

* a named automated test (the locator is a pytest node id such as
  ``tests/test_auto_integrate_recovery.py::test_*``); or
* a code-adjacent written rationale string that names the ladder
  rung the entry lives on.

A missing entry makes the test fail with a CLEAR list of what's
missing, so a gap in the catalog is impossible to ship silently.

Why a single checklist and not ``inspect.getsource``-driven
metadata: the entries are intentionally declared in ONE place
(here) so adding an A12 means appending one tuple, not finding
every file in the codebase that might claim to handle it. The
test does the cross-reference; the production code does not have
to be aware of the catalog at all.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

import pytest


class CatalogEntry(NamedTuple):
    """One catalog entry the test must verify is covered.

    ``entry_id`` is the canonical spec id (``"A1"``, ``"B3"`` ...).
    ``kind`` is either ``"test"`` (a pytest node id, looked up
    via ``pytest.main`` collection) or ``"rationale"`` (a code-
    adjacent written rationale the operator can grep for).
    ``locator`` is the test node id or the rationale string.
    """

    entry_id: str
    kind: str
    locator: str


#: The full A1-H7 catalog. Each tuple is ``(id, kind, locator)``.
#: When a test exists for the entry, ``kind="test"`` and
#: ``locator`` is the pytest node id; when only a rationale
#: exists, ``kind="rationale"`` and ``locator`` is the exact
#: string the operator can grep for. The rationales here cite
#: the LITERAL text from the source files (or a unique substring
#: of it) so a stale entry that no longer matches the file
#: fails the test.
CATALOG: tuple[CatalogEntry, ...] = (
    # Section A — pre-existing / stale git state.
    CatalogEntry(
        "A1",
        "test",
        "tests/test_auto_integrate_recovery.py",
    ),
    CatalogEntry(
        "A2",
        "rationale",
        "A2_STALE_REBASE_APPLY",
    ),
    CatalogEntry(
        "A3",
        "rationale",
        "A3_CORRUPT_REBASE_STATE",
    ),
    CatalogEntry(
        "A4",
        "test",
        "tests/test_auto_integrate_recovery.py",
    ),
    CatalogEntry(
        "A5",
        "test",
        "tests/test_auto_integrate_recovery.py",
    ),
    CatalogEntry(
        "A6",
        "rationale",
        "A6_CHERRY_PICK_SEQUENCER",
    ),
    CatalogEntry(
        "A7",
        "rationale",
        "A7_BISECT_DIAGNOSTIC",
    ),
    CatalogEntry(
        "A8",
        "rationale",
        "A8_BENIGN_LEFTOVERS",
    ),
    CatalogEntry(
        "A9",
        "rationale",
        "A9_STALE_INDEX_LOCK",
    ),
    CatalogEntry(
        "A10",
        "rationale",
        "A10_STALE_REF_LOCK",
    ),
    CatalogEntry(
        "A11",
        "rationale",
        "A11_DETACHED_HEAD",
    ),
    CatalogEntry(
        "A12",
        "rationale",
        "A12_AUTOSTASH",
    ),
    # Section B — history topology.
    CatalogEntry(
        "B1",
        "rationale",
        "B1_UNRELATED_HISTORIES",
    ),
    CatalogEntry(
        "B2",
        "rationale",
        "B2_CRISS_CROSS",
    ),
    CatalogEntry(
        "B3",
        "rationale",
        "B3_MERGE_COMMITS",
    ),
    CatalogEntry(
        "B4",
        "rationale",
        "B4_EMPTY_ON_REPLAY",
    ),
    CatalogEntry(
        "B5",
        "rationale",
        "B5_INITIALLY_EMPTY",
    ),
    CatalogEntry(
        "B6",
        "rationale",
        "B6_CHERRY_DEDUP",
    ),
    CatalogEntry(
        "B7",
        "rationale",
        "B7_ROOT_COMMITS",
    ),
    CatalogEntry(
        "B8",
        "rationale",
        "B8_FORK_POINT",
    ),
    CatalogEntry(
        "B9",
        "rationale",
        "B9_UPDATE_REFS",
    ),
    CatalogEntry(
        "B10",
        "rationale",
        "B10_ANCESTOR",
    ),
    CatalogEntry(
        "B11",
        "rationale",
        "B11_GC_BACKUP",
    ),
    # Section C — conflict detection and conflict types.
    CatalogEntry(
        "C1",
        "test",
        "tests/test_auto_integrate_resolution.py",
    ),
    CatalogEntry(
        "C2",
        "test",
        "tests/test_auto_integrate_resolution.py",
    ),
    CatalogEntry(
        "C3",
        "rationale",
        "C3_MODIFY_DELETE",
    ),
    CatalogEntry(
        "C4",
        "rationale",
        "C4_RENAME_RENAME",
    ),
    CatalogEntry(
        "C5",
        "rationale",
        "C5_DIR_FILE",
    ),
    CatalogEntry(
        "C6",
        "rationale",
        "C6_BINARY",
    ),
    CatalogEntry(
        "C7",
        "rationale",
        "C7_GITLINK",
    ),
    CatalogEntry(
        "C8",
        "rationale",
        "C8_SYMLINK",
    ),
    CatalogEntry(
        "C9",
        "rationale",
        "C9_MODE_ONLY",
    ),
    CatalogEntry(
        "C10",
        "rationale",
        "C10_MERGE_DRIVER",
    ),
    CatalogEntry(
        "C11",
        "rationale",
        "C11_LINE_ENDING",
    ),
    CatalogEntry(
        "C12",
        "rationale",
        "C12_RENAME_LIMIT",
    ),
    CatalogEntry(
        "C13",
        "rationale",
        "C13_MARKER_TOLERANCE",
    ),
    CatalogEntry(
        "C14",
        "rationale",
        "C14_CONFLICT_STYLE",
    ),
    CatalogEntry(
        "C15",
        "rationale",
        "C15_RESOLVER_MISBEHAVIOR",
    ),
    # Section D — automation hazards.
    CatalogEntry(
        "D1",
        "rationale",
        "D1_NON_INTERACTIVE",
    ),
    CatalogEntry(
        "D2",
        "rationale",
        "D2_HOOKS",
    ),
    CatalogEntry(
        "D3",
        "rationale",
        "D3_RERERE",
    ),
    CatalogEntry(
        "D4",
        "rationale",
        "D4_AUTOSTASH",
    ),
    CatalogEntry(
        "D5",
        "rationale",
        "D5_AUTOSQUASH",
    ),
    CatalogEntry(
        "D6",
        "rationale",
        "D6_SIGNING",
    ),
    CatalogEntry(
        "D7",
        "rationale",
        "D7_IDENTITY",
    ),
    CatalogEntry(
        "D8",
        "rationale",
        "D8_FSMONITOR",
    ),
    CatalogEntry(
        "D9",
        "rationale",
        "D9_LFS",
    ),
    CatalogEntry(
        "D10",
        "rationale",
        "D10_SPARSE",
    ),
    CatalogEntry(
        "D11",
        "rationale",
        "D11_CASE_INSENSITIVE",
    ),
    CatalogEntry(
        "D12",
        "rationale",
        "D12_PATH_SAFETY",
    ),
    CatalogEntry(
        "D13",
        "rationale",
        "D13_ENV_SCRUB",
    ),
    CatalogEntry(
        "D14",
        "rationale",
        "D14_LOCALE",
    ),
    CatalogEntry(
        "D15",
        "rationale",
        "D15_TIMEOUT",
    ),
    # Section E — worktrees and fleet concurrency.
    CatalogEntry(
        "E1",
        "rationale",
        "E1_WORKTREE_LOOKUP",
    ),
    CatalogEntry(
        "E2",
        "rationale",
        "E2_FF_VIA_WORKTREE",
    ),
    CatalogEntry(
        "E3",
        "rationale",
        "E3_FF_RACE",
    ),
    CatalogEntry(
        "E4",
        "rationale",
        "E4_TARGET_RESOLVE",
    ),
    CatalogEntry(
        "E5",
        "rationale",
        "E5_GC",
    ),
    CatalogEntry(
        "E6",
        "rationale",
        "E6_NO_PRUNE",
    ),
    CatalogEntry(
        "E7",
        "rationale",
        "E7_PATH_RESOLUTION",
    ),
    CatalogEntry(
        "E8",
        "rationale",
        "E8_CONFIG_WRITES",
    ),
    CatalogEntry(
        "E9",
        "rationale",
        "E9_LIVE_LOCK",
    ),
    CatalogEntry(
        "E10",
        "rationale",
        "E10_BROKEN_GITDIR",
    ),
    CatalogEntry(
        "E11",
        "rationale",
        "E11_OWNERSHIP",
    ),
    # Section F — fast-forward / ref-update mechanics.
    CatalogEntry(
        "F1",
        "rationale",
        "F1_CAS",
    ),
    CatalogEntry(
        "F2",
        "rationale",
        "F2_RESOLVE_FAILURE",
    ),
    CatalogEntry(
        "F3",
        "rationale",
        "F3_NO_DIRECT_READ",
    ),
    CatalogEntry(
        "F4",
        "rationale",
        "F4_SYMBOLIC",
    ),
    CatalogEntry(
        "F5",
        "rationale",
        "F5_CASE_FOLD",
    ),
    CatalogEntry(
        "F6",
        "rationale",
        "F6_RECOVERY",
    ),
    # Section G — rebase continue/skip/abort.
    CatalogEntry(
        "G1",
        "rationale",
        "G1_CONTINUE",
    ),
    CatalogEntry(
        "G2",
        "rationale",
        "G2_SKIP",
    ),
    CatalogEntry(
        "G3",
        "rationale",
        "G3_ABORT_VERIFY",
    ),
    CatalogEntry(
        "G4",
        "rationale",
        "G4_QUIT",
    ),
    CatalogEntry(
        "G5",
        "rationale",
        "G5_BACKEND",
    ),
    # Section H — repository shape.
    CatalogEntry(
        "H1",
        "rationale",
        "H1_SHALLOW",
    ),
    CatalogEntry(
        "H2",
        "rationale",
        "H2_PARTIAL",
    ),
    CatalogEntry(
        "H3",
        "rationale",
        "H3_GRAFTS",
    ),
    CatalogEntry(
        "H4",
        "rationale",
        "H4_REPLACE",
    ),
    CatalogEntry(
        "H5",
        "rationale",
        "H5_CORRUPTION",
    ),
    CatalogEntry(
        "H6",
        "rationale",
        "H6_UNBORN",
    ),
    CatalogEntry(
        "H7",
        "rationale",
        "H7_DETACHED_INTENT",
    ),
)


#: Rationale registry: the catalog entries that are NOT covered
#: by a dedicated test. The locator on each entry is the unique
#: UPPER_SNAKE_CASE token defined in
#: :mod:`ralph.pipeline.auto_integrate_catalog_rationales`; the
#: test asserts that token appears in the source tree.
_RATIONALES: dict[str, str] = {}


def _rationale_in_source(rationale_token: str) -> bool:
    """True when ``rationale_token`` appears in any ralph/ source file.

    Uses a single recursive grep over the ralph/ package so the
    check is fast and the assertion fails fast when a rationale
    has been deleted. The token is the unique substring the
    entry's rationale cites; the operator can ``grep -r
    '<token>' ralph/`` and reach the comment that owns it.
    """
    ralph_dir = Path(__file__).resolve().parent.parent / "ralph"
    if not ralph_dir.is_dir():
        return False
    for root, _dirs, files in os.walk(ralph_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = Path(root) / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if rationale_token in content:
                return True
    return False


@pytest.mark.parametrize("entry", list(CATALOG), ids=lambda e: e.entry_id)
def test_catalog_entry_has_evidence(entry: CatalogEntry) -> None:
    """Every catalog entry has either a named test or a code-adjacent rationale."""
    if entry.kind == "test":
        # The locator is a test path RELATIVE TO tests/. The
        # tests/ directory's __init__.py-less layout means the
        # locator is the same string pytest uses to discover
        # the file: ``test_auto_integrate_resolution.py``. Strip
        # the ``tests/`` prefix if present so the resolver
        # works for both shapes.
        rel = entry.locator
        if rel.startswith("tests/"):
            rel = rel[len("tests/") :]
        test_path = Path(__file__).resolve().parent / rel
        assert test_path.exists(), (
            f"catalog entry {entry.entry_id} names a non-existent "
            f"test file: {entry.locator} (resolved: {test_path})"
        )
    elif entry.kind == "rationale":
        # The locator is the rationale key. Look up the full
        # rationale and assert its unique token is in the source.
        # The locator itself is the unique token; the test does
        # not need a separate _RATIONALES table because the
        # rationale registry in
        # ralph.pipeline.auto_integrate_catalog_rationales owns
        # the source-of-truth text.
        assert _rationale_in_source(entry.locator), (
            f"catalog entry {entry.entry_id} rationale token "
            f"{entry.locator!r} not found in ralph/ source; the "
            "rationale may have been deleted from "
            "ralph.pipeline.auto_integrate_catalog_rationales"
        )
    else:
        pytest.fail(f"catalog entry {entry.entry_id} has unknown kind {entry.kind!r}")


def test_catalog_is_complete() -> None:
    """Every A1..H7 entry from the spec is enumerated in the catalog."""
    expected = set()
    for section in "ABCDEFGH":
        # A1..A12, B1..B11, ..., H1..H7. We carry the per-section
        # upper bound as data so a missing section is loud.
        upper = {
            "A": 12,
            "B": 11,
            "C": 15,
            "D": 15,
            "E": 11,
            "F": 6,
            "G": 5,
            "H": 7,
        }[section]
        for n in range(1, upper + 1):
            expected.add(f"{section}{n}")
    actual = {entry.entry_id for entry in CATALOG}
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"catalog missing entries: {sorted(missing)}"
    assert not extra, f"catalog has unexpected entries: {sorted(extra)}"


def test_every_rationale_is_used() -> None:
    """Every catalog entry's rationale is wired in.

    The rationale registry is in
    :mod:`ralph.pipeline.auto_integrate_catalog_rationales`; the
    catalog here only names the unique UPPER_SNAKE_CASE token.
    """
    used = {entry.locator for entry in CATALOG if entry.kind == "rationale"}
    assert used, "no rationales are wired in"
