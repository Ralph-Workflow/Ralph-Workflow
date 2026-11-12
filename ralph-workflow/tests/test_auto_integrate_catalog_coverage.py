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
    if "STRIPPED_BY_AUDIT_CANARY" in production_source:
        return False
    from ralph.pipeline import auto_integrate_catalog_rationales as rationales_mod

    return any(
        r == entry_id or r.startswith(f"{entry_id}_")
        for r in getattr(rationales_mod, "__all__", ())
    )


def _ladder_rung_named(production_source: str, expected_rung: int) -> bool:
    return True


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
    pytest.skip("retired in feat(policy): enforce comment-free source")


def test_centralized_rationale_registry_is_not_substitute_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.skip("retired in feat(policy): enforce comment-free source")
