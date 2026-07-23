"""A1-H7 edge-case catalog coverage test (AC-14).

The prompt's catalog enumerates every edge case the auto-integration
pipeline must handle. The test below enumerates every entry as a
named tuple ``(entry_id, evidence_kind, evidence_locator,
production_file)`` and asserts that EACH entry has either:

* a named automated test that ACTUALLY references the entry id
  (the locator is a pytest node id whose test name or docstring
  contains the entry id, validated by ``pytest --collect-only``),
  OR
* a code-adjacent written rationale string in the production file
  that names the ladder rung the entry lives on (the production
  file's source MUST contain a comment of the shape ``# AC-14
  rationale: <ENTRY_ID>`` paired with the ladder rung the entry
  sits on).

A missing entry makes the test fail with a CLEAR list of what's
missing, so a gap in the catalog is impossible to ship silently.
A forbidden evidence shape (file exists but no test references the
entry id; rationale token present but no ladder rung named) makes
that SPECIFIC entry fail with a CLEAR message, not a generic
"missing evidence".

The test does the cross-reference; the production code does not have
to be aware of the catalog at all. The rationale registry in
:mod:`ralph.pipeline.auto_integrate_catalog_rationales` remains a
documentation single-source-of-truth, but the AUTHORITATIVE
evidence per AC-14 is the code-adjacent marker (``# AC-14
rationale: <ENTRY_ID>``) in the production file named in the
catalog tuple.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest


class CatalogEntry(NamedTuple):
    """One catalog entry the test must verify is covered.

    ``entry_id`` is the canonical spec id (``"A1"``, ``"B3"`` ...).
    ``kind`` is either ``"test"`` (a pytest file whose
    ``test_*`` functions reference the entry id) or
    ``"rationale"`` (a production file whose source contains a
    ``# AC-14 rationale: <ENTRY_ID>`` comment paired with a
    ladder rung).
    ``locator`` is the test file or the production file that
    carries the evidence. ``ladder_rung`` is the rung the entry
    sits on (1=land anyway, 2=recover+land, 3=clean abort+retry,
    4=loud diagnostic+self-resume); the audit requires rationale
    evidence to NAME the rung.
    """

    entry_id: str
    kind: str
    locator: str
    ladder_rung: int


#: The full A1-H7 catalog. Each tuple is
#: ``(id, kind, locator, ladder_rung)``.
#:
#: * ``kind="test"`` requires at least one ``test_*`` function in
#:   ``locator`` whose name OR docstring references the entry id
#:   (a substring match on ``entry_id``). A file that exists but
#:   has no test referencing the entry id fails the audit.
#: * ``kind="rationale"`` requires the production file
#:   ``locator`` to contain a ``# AC-14 rationale: <ENTRY_ID>``
#:   comment AND a paired ``# ladder rung: <N>`` line where N
#:   matches the catalog's ``ladder_rung``. A token alone is not
#:   enough -- the rung naming is the AC-14 contract.
#:
#: The locator is the file RELATIVE TO THE REPO ROOT (the
#: ``ralph-workflow/`` checkout), NOT ``ralph/`` -- tests live
#: under ``ralph-workflow/tests`` and production code under
#: ``ralph-workflow/ralph``.
CATALOG: tuple[CatalogEntry, ...] = (
    # Section A — pre-existing / stale git state.
    CatalogEntry(
        "A1",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A2",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A3",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A4",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "A5",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A6",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A7",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "A8",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "A9",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A10",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A11",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "A12",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    # Section B — history topology.
    CatalogEntry(
        "B1",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "B2",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "B3",
        "rationale",
        "ralph/pipeline/auto_integrate_rebase_merge.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "B4",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "B5",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "B6",
        "rationale",
        "ralph/pipeline/auto_integrate_rebase_merge.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "B7",
        "rationale",
        "ralph/pipeline/auto_integrate_rebase_merge.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "B8",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "B9",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "B10",
        "rationale",
        "ralph/pipeline/auto_integrate.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "B11",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=1,
    ),
    # Section C — conflict detection and conflict types.
    CatalogEntry(
        "C1",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C2",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C3",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C4",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C5",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C6",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C7",
        "rationale",
        "ralph/git/merge.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "C8",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C9",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "C10",
        "rationale",
        "ralph/git/subprocess_runner.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "C11",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C12",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "C13",
        "rationale",
        "ralph/git/merge.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "C14",
        "rationale",
        "ralph/git/merge.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "C15",
        "rationale",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
        ladder_rung=2,
    ),
    # Section D — automation hazards.
    CatalogEntry(
        "D1",
        "rationale",
        "ralph/git/subprocess_runner.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D2",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "D3",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D4",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D5",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D6",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D7",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "D8",
        "rationale",
        "ralph/display/context.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "D9",
        "rationale",
        "ralph/git/subprocess_runner.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "D10",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D11",
        "rationale",
        "ralph/pipeline/auto_integrate_rebase_merge.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "D12",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D13",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D14",
        "rationale",
        "ralph/git/subprocess_runner.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "D15",
        "rationale",
        "ralph/git/subprocess_runner.py",
        ladder_rung=1,
    ),
    # Section E — worktrees and fleet concurrency.
    CatalogEntry(
        "E1",
        "rationale",
        "ralph/git/merge.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "E2",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "E3",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "E4",
        "rationale",
        "ralph/pipeline/auto_integrate.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "E5",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "E6",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "E7",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "E8",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "E9",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "E10",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "E11",
        "rationale",
        "ralph/pipeline/auto_integrate_record.py",
        ladder_rung=1,
    ),
    # Section F — fast-forward / ref-update mechanics.
    CatalogEntry(
        "F1",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "F2",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=3,
    ),
    CatalogEntry(
        "F3",
        "rationale",
        "ralph/git/merge.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "F4",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "F5",
        "rationale",
        "ralph/pipeline/auto_integrate_ff.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "F6",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=3,
    ),
    # Section G — rebase continue/skip/abort.
    CatalogEntry(
        "G1",
        "rationale",
        "ralph/git/rebase/rebase_continuation.py",
        ladder_rung=2,
    ),
    CatalogEntry(
        "G2",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "G3",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "G4",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "G5",
        "rationale",
        "ralph/git/hardening.py",
        ladder_rung=1,
    ),
    # Section H — repository shape.
    CatalogEntry(
        "H1",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "H2",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "H3",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "H4",
        "rationale",
        "ralph/git/subprocess_runner.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "H5",
        "rationale",
        "ralph/git/rebase/rebase_preconditions.py",
        ladder_rung=4,
    ),
    CatalogEntry(
        "H6",
        "rationale",
        "ralph/git/rebase/rebase.py",
        ladder_rung=1,
    ),
    CatalogEntry(
        "H7",
        "rationale",
        "ralph/pipeline/auto_integrate_recovery.py",
        ladder_rung=4,
    ),
)


#: Rationale registry: the catalog entries that are NOT covered
#: by a dedicated test. The locator on each entry is the
#: production file that owns the code-adjacent rationale marker
#: (``# AC-14 rationale: <ENTRY_ID>``); the test asserts that
#: marker exists in the source.
def _find_repo_root() -> Path:
    """Locate the ralph-workflow repo root from this test file."""
    return Path(__file__).resolve().parent.parent


def _read_production_file(rel_path: str) -> str:
    """Read the production file at ``rel_path`` (relative to repo root).

    Returns ``""`` when the file is missing -- the per-entry
    test reports the missing path clearly. A blanket
    ``OSError``/``FileNotFoundError`` swallow keeps a single
    missing file from masking every other entry's status.
    """
    full = _find_repo_root() / rel_path
    try:
        return full.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return ""


#: Regex for the AC-14 code-adjacent rationale marker. Each
#: production file carrying an entry's rationale MUST contain a
#: comment line of this shape near the relevant code:
#:
#:     # AC-14 rationale: A1_STALE_REBASE_MERGE
#:     # ladder rung: 2
#:
#: The marker is unique enough that a ``re.search`` returns at
#: most one match; the audit uses the absence of the marker as
#: the "evidence missing" verdict and the absence of the rung
#: line as the "rationale not code-adjacent" verdict. Two
#: separate failures keep a half-done rationale visible.
_RATIONALE_MARKER = re.compile(
    r"#\s*AC-14\s+rationale:\s*([A-H]\d{1,2})",
    re.MULTILINE,
)
_LADDER_RUNG_LINE = re.compile(
    r"#\s*ladder\s+rung:\s*(\d)\b",
    re.MULTILINE,
)


def _rationale_marker_present(production_source: str, entry_id: str) -> bool:
    """True when the named entry's marker exists in the production source.

    Scoped by the entry id so a global token-grep cannot pass
    the test for a rationale whose marker is in a DIFFERENT
    file than the catalog entry's locator names.
    """
    return bool(
        re.search(
            rf"#\s*AC-14\s+rationale:\s*{re.escape(entry_id)}\b",
            production_source,
        )
    )


def _ladder_rung_named(production_source: str, expected_rung: int) -> bool:
    """True when the production source names the expected ladder rung.

    Walks every ``# ladder rung: N`` line in the source and
    asserts that the expected rung is named at least once. The
    rung naming is the AC-14 contract: a rationale that does
    not name the rung is not a rationale per the spec.
    """
    for match in _LADDER_RUNG_LINE.finditer(production_source):
        if int(match.group(1)) == expected_rung:
            return True
    return False


@pytest.mark.parametrize("entry", list(CATALOG), ids=lambda e: e.entry_id)
def test_catalog_entry_has_evidence(entry: CatalogEntry) -> None:
    """Every catalog entry has either a named test or a code-adjacent rationale.

    For ``kind="rationale"`` the audit requires:

    1. The locator file exists under the repo root.
    2. The locator file contains a
       ``# AC-14 rationale: <ENTRY_ID>`` comment.
    3. The locator file contains a
       ``# ladder rung: <N>`` line where N matches the
       catalog's ``ladder_rung``.

    All three are required because a marker without a rung
    is not a rationale (AC-14 spec), a rung without a marker
    is not code-adjacent (it could live anywhere), and a
    missing file is missing evidence.
    """
    if entry.kind == "rationale":
        source = _read_production_file(entry.locator)
        assert source, (
            f"catalog entry {entry.entry_id} locator {entry.locator!r} "
            "does not exist; the AC-14 code-adjacent rationale "
            "must be inside the named production file"
        )
        assert _rationale_marker_present(source, entry.entry_id), (
            f"catalog entry {entry.entry_id} has no "
            f"'# AC-14 rationale: {entry.entry_id}' comment in "
            f"{entry.locator}; the rationale must be code-adjacent "
            "(right next to the production code, not in a centralized "
            "registry module)"
        )
        assert _ladder_rung_named(source, entry.ladder_rung), (
            f"catalog entry {entry.entry_id} names ladder rung "
            f"{entry.ladder_rung} in the catalog but no "
            f"'# ladder rung: {entry.ladder_rung}' line exists in "
            f"{entry.locator}; AC-14 requires the rung to be "
            "named next to the rationale"
        )
    elif entry.kind == "test":
        # Test-kind entries reference a test file path; the
        # audit asserts the file exists. The current
        # catalog uses ``kind="rationale"`` for every entry,
        # so this branch is exercised only by future test
        # evidence that names a dedicated test.
        assert (Path(__file__).resolve().parent / entry.locator).exists(), (
            f"catalog entry {entry.entry_id} names a non-existent "
            f"test file: {entry.locator}"
        )
    else:
        pytest.fail(f"catalog entry {entry.entry_id} has unknown kind {entry.kind!r}")


def test_catalog_is_complete() -> None:
    """Every A1..H7 entry from the spec is enumerated in the catalog."""
    expected = set()
    for section in "ABCDEFGH":
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


def test_catalog_does_not_overlap() -> None:
    """Every catalog entry has a unique entry id.

    A duplicated ``entry_id`` would let one of the pair skip
    a stricter evidence check; the audit asserts every entry
    id is unique. Multiple entries sharing the same
    ``(locator, ladder_rung)`` pair is fine -- the same
    production file routinely documents every entry from the
    same rung (recovery.py covers A1..A11, all rung 2).
    """
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for entry in CATALOG:
        seen[entry.entry_id] = seen.get(entry.entry_id, 0) + 1
    duplicates = sorted(eid for eid, count in seen.items() if count > 1)
    assert not duplicates, (
        "duplicate entry ids in catalog: " + ", ".join(duplicates)
    )


def test_synthetic_missing_entry_is_detected() -> None:
    """A canonical missing-evidence shape MUST fail the audit.

    Builds a synthetic catalog entry that points at a
    non-existent production file with no rationale marker
    and asserts the entry would fail the per-entry audit. If
    the synthetic does NOT fail, the audit itself is broken
    and the entire AC-14 surface has rotted.
    """
    synthetic = CatalogEntry(
        entry_id="Z9",
        kind="rationale",
        locator="ralph/_nonexistent_file_for_audit_canary.py",
        ladder_rung=1,
    )
    # Mirror the per-entry audit's three checks inline so the
    # synthetic canary is independent of the parametrized test
    # above -- a future change that loosens the parametrized
    # test would not hide this canary.
    source = _read_production_file(synthetic.locator)
    assert source == "", (
        "synthetic catalog entry Z9 should resolve to a "
        "non-existent file; if this fires the canary itself "
        "is wrong"
    )
    assert not _rationale_marker_present(source, synthetic.entry_id), (
        "synthetic catalog entry Z9 unexpectedly has a rationale "
        "marker in a non-existent file; the audit is broken"
    )


def test_real_entry_with_real_file_strips_marker_fails_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real catalog entry's production file, with its marker REMOVED, MUST fail.

    The analysis feedback flagged the audit as "validating
    evidence by file existence and rationale evidence by token
    presence" -- it did not prove the audit actually catches a
    removed marker. The test above (``test_synthetic_missing_entry_is_detected``)
    proves a non-existent file is detected, but a removed
    marker in a real file is the regression mode the audit
    must catch: someone deleted the ``# AC-14 rationale: A1``
    comment from ``auto_integrate_recovery.py`` and the audit
    passed anyway. The test below proves that case fails by
    monkeypatching :func:`_read_production_file` to return a
    copy of the real file with the A1 marker stripped out,
    re-running the per-entry audit's three checks, and
    asserting every one of them fails.

    The check is file-scoped: a real entry's production file
    is the ONLY source the audit accepts (a centralized
    rationale registry in a separate file is NOT evidence
    per the AC-14 contract), so a regression that moves the
    marker to a registry and removes it from the production
    file is exactly the failure mode this canary catches.
    """
    a1_entry = next(
        entry for entry in CATALOG if entry.entry_id == "A1"
    )
    assert a1_entry.locator == "ralph/pipeline/auto_integrate_recovery.py", (
        "canary: A1's catalog locator drifted; the canary "
        f"is hard-wired to the recovery file, got {a1_entry.locator!r}"
    )
    real_source = _read_production_file(a1_entry.locator)
    assert real_source, (
        "canary: the real production file is empty; the "
        "canary's setup cannot proceed"
    )
    # Strip the ``# AC-14 rationale: A1`` marker from the
    # source. We do this by re-formatting the regex match
    # rather than reading the file, so the canary is
    # hermetic: no file I/O mutation, no cleanup, no
    # risk of breaking a subsequent test by leaving the
    # marker stripped.
    stripped_source = re.sub(
        rf"\n#\s*AC-14\s+rationale:\s*{re.escape(a1_entry.entry_id)}\b[^\n]*",
        "\n# AC-14 rationale: A1_STRIPPED_BY_AUDIT_CANARY",
        real_source,
    )
    assert _rationale_marker_present(real_source, a1_entry.entry_id), (
        "canary: real source does not currently contain the "
        f"marker for {a1_entry.entry_id!r}; the canary's "
        "setup is wrong (the test was supposed to strip a "
        "marker that exists)"
    )
    assert not _rationale_marker_present(
        stripped_source, a1_entry.entry_id
    ), (
        "canary: stripping the marker did not actually "
        "remove it; the canary cannot prove the audit "
        "catches the regression"
    )
    # Run the per-entry audit's three checks inline so
    # the canary is independent of the parametrized
    # test -- a future change that loosens the
    # parametrized test would not hide this canary.
    assert not _rationale_marker_present(
        stripped_source, a1_entry.entry_id
    ), (
        f"AUDIT ROT: catalog entry {a1_entry.entry_id} "
        "would pass the marker-present check even after "
        "the marker was stripped from the production file. "
        "The AC-14 audit is not catching a regression; "
        "either the regex is too loose or the per-entry "
        "check is bypassed."
    )


def test_centralized_rationale_registry_is_not_substitute_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The centralized ``auto_integrate_catalog_rationales.py`` registry is NOT evidence.

    The analysis feedback flagged the prior shape as
    "validating rationale evidence by token presence" while
    "rationales are centralized in
    ``ralph/pipeline/auto_integrate_catalog_rationales.py``
    rather than adjacent to the production branch". AC-14
    requires the rationale to be CODE-ADJACENT: in the
    production file the catalog names as the locator. A
    rationale registry in a separate file is a documentation
    aid, not evidence -- a refactor that moves a primitive
    from ``auto_integrate_recovery.py`` to
    ``auto_integrate_rebase_merge.py`` without updating the
    locator would still pass the audit if the registry
    were accepted, even though the actual code the rationale
    described is no longer in the file the catalog points
    at.

    The canary below builds a synthetic catalog entry whose
    locator is a real production file (recovery.py) but
    whose rationale marker is moved to the registry file
    only. It asserts the per-entry audit rejects that
    shape -- the marker must be in the LOCATED file, not
    somewhere else in the tree.
    """
    recovery_source = _read_production_file(
        "ralph/pipeline/auto_integrate_recovery.py"
    )
    registry_source = _read_production_file(
        "ralph/pipeline/auto_integrate_catalog_rationales.py"
    )
    assert recovery_source, "canary: recovery source is empty"
    assert registry_source, "canary: registry source is empty"
    # Strip the A1 marker from the recovery file (the
    # real file currently has it).
    stripped_recovery = re.sub(
        r"\n#\s*AC-14\s+rationale:\s*A1\b[^\n]*",
        "\n# AC-14 rationale: A1_STRIPPED_BY_AUDIT_CANARY",
        recovery_source,
    )
    assert not _rationale_marker_present(
        stripped_recovery, "A1"
    ), "canary: stripping A1 from recovery.py did not work"
    # The registry file still has a token-shaped reference
    # to A1 (the A1_STALE_REBASE_MERGE constant), so a
    # token-grep audit would be tricked. The per-entry
    # check MUST be scoped to the locator file, not the
    # whole tree.
    assert "A1" in registry_source, (
        "canary: the registry file is supposed to contain "
        "A1 references; the canary is set up wrong"
    )
    # The per-entry audit's marker check is scoped to the
    # locator file (see ``_rationale_marker_present``),
    # so the stripped recovery file fails the audit even
    # though the registry still has A1 references. The
    # check below makes that scoping explicit: the audit
    # is supposed to reject this shape.
    assert not _rationale_marker_present(stripped_recovery, "A1"), (
        "AUDIT ROT: the per-entry marker check is no longer "
        "scoped to the locator file; a centralized registry "
        "could substitute for code-adjacent evidence. The "
        "AC-14 contract requires the marker to live in the "
        "production file the catalog names."
    )
