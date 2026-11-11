from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest


class CatalogEntry(NamedTuple):
    entry_id: str
    kind: str
    locator: str
    ladder_rung: int


CATALOG: tuple[CatalogEntry, ...] = (
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


def _find_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_production_file(rel_path: str) -> str:
    full = _find_repo_root() / rel_path
    try:
        return full.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return ""


_RATIONALE_MARKER = re.compile(
    r"#\s*AC-14\s+rationale:\s*([A-H]\d{1,2})",
    re.MULTILINE,
)
_LADDER_RUNG_LINE = re.compile(
    r"#\s*ladder\s+rung:\s*(\d)\b",
    re.MULTILINE,
)


def _rationale_marker_present(production_source: str, entry_id: str) -> bool:
    return bool(
        re.search(
            rf"#\s*AC-14\s+rationale:\s*{re.escape(entry_id)}\b",
            production_source,
        )
    )


def _ladder_rung_named(production_source: str, expected_rung: int) -> bool:
    for match in _LADDER_RUNG_LINE.finditer(production_source):
        if int(match.group(1)) == expected_rung:
            return True
    return False


@pytest.mark.parametrize("entry", list(CATALOG), ids=lambda e: e.entry_id)
def test_catalog_entry_has_evidence(entry: CatalogEntry) -> None:
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
        assert (Path(__file__).resolve().parent / entry.locator).exists(), (
            f"catalog entry {entry.entry_id} names a non-existent test file: {entry.locator}"
        )
    else:
        pytest.fail(f"catalog entry {entry.entry_id} has unknown kind {entry.kind!r}")


def test_catalog_is_complete() -> None:
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
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for entry in CATALOG:
        seen[entry.entry_id] = seen.get(entry.entry_id, 0) + 1
    duplicates = sorted(eid for eid, count in seen.items() if count > 1)
    assert not duplicates, "duplicate entry ids in catalog: " + ", ".join(duplicates)


def test_synthetic_missing_entry_is_detected() -> None:
    synthetic = CatalogEntry(
        entry_id="Z9",
        kind="rationale",
        locator="ralph/_nonexistent_file_for_audit_canary.py",
        ladder_rung=1,
    )
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
    a1_entry = next(entry for entry in CATALOG if entry.entry_id == "A1")
    assert a1_entry.locator == "ralph/pipeline/auto_integrate_recovery.py", (
        "canary: A1's catalog locator drifted; the canary "
        f"is hard-wired to the recovery file, got {a1_entry.locator!r}"
    )
    real_source = _read_production_file(a1_entry.locator)
    assert real_source, (
        "canary: the real production file is empty; the canary's setup cannot proceed"
    )
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
    assert not _rationale_marker_present(stripped_source, a1_entry.entry_id), (
        "canary: stripping the marker did not actually "
        "remove it; the canary cannot prove the audit "
        "catches the regression"
    )
    assert not _rationale_marker_present(stripped_source, a1_entry.entry_id), (
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
    recovery_source = _read_production_file("ralph/pipeline/auto_integrate_recovery.py")
    registry_source = _read_production_file("ralph/pipeline/auto_integrate_catalog_rationales.py")
    assert recovery_source, "canary: recovery source is empty"
    assert registry_source, "canary: registry source is empty"
    stripped_recovery = re.sub(
        r"\n#\s*AC-14\s+rationale:\s*A1\b[^\n]*",
        "\n# AC-14 rationale: A1_STRIPPED_BY_AUDIT_CANARY",
        recovery_source,
    )
    assert not _rationale_marker_present(stripped_recovery, "A1"), (
        "canary: stripping A1 from recovery.py did not work"
    )
    assert "A1" in registry_source, (
        "canary: the registry file is supposed to contain A1 references; the canary is set up wrong"
    )
    assert not _rationale_marker_present(stripped_recovery, "A1"), (
        "AUDIT ROT: the per-entry marker check is no longer "
        "scoped to the locator file; a centralized registry "
        "could substitute for code-adjacent evidence. The "
        "AC-14 contract requires the marker to live in the "
        "production file the catalog names."
    )
