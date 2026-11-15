from __future__ import annotations

from ralph.git.diff_entry import DiffEntry
from ralph.phases.commit_review import (
    TripwireThresholds,
    evaluate_tripwires,
)

_TRACKED_PATHS = frozenset(
    {
        "package.json",
        "uv.lock",
        "src/app.py",
        "src/other.py",
        "docs/readme.md",
        "docs/guide.md",
        "tests/test_a.py",
        "tests/test_b.py",
        "scripts/run.sh",
        "config/settings.toml",
    }
)


def _entry(
    path: str,
    *,
    status: str = "M",
    content: str | None = '{"name": "demo"}',
    size: int = 18,
    new_mode: str = "100644",
    is_binary: bool = False,
) -> DiffEntry:
    return DiffEntry(
        path,
        status,
        "100644",
        new_mode,
        size,
        is_binary,
        content,
        _TRACKED_PATHS,
    )


def _report(entry: DiffEntry):
    return evaluate_tripwires([entry], len(_TRACKED_PATHS), TripwireThresholds())


def test_destructive_manifest_overwrite_is_a_hard_protected_finding() -> None:
    report = _report(_entry("package.json", content="   "))

    assert [finding.category for finding in report.hard] == ["protected_overwrite"]
    assert report.hard[0].path == "package.json"
    assert report.soft == ()


def test_manifest_replaced_by_symlink_is_a_hard_protected_finding() -> None:
    report = _report(_entry("package.json", new_mode="120000"))

    assert [finding.category for finding in report.hard] == ["protected_overwrite"]
    assert report.soft == ()


def test_manifest_replaced_by_binary_is_a_hard_protected_finding() -> None:
    report = _report(_entry("package.json", is_binary=True))

    assert [finding.category for finding in report.hard] == ["protected_overwrite"]
    assert report.soft == ()


def test_ordinary_manifest_overwrite_is_soft_evidence_only() -> None:
    report = _report(_entry("package.json"))

    assert report.hard == ()
    assert [finding.category for finding in report.soft] == ["protected_overwrite"]
    assert report.soft[0].path == "package.json"


def test_ordinary_lockfile_overwrite_is_soft_evidence_only() -> None:
    report = _report(_entry("uv.lock", content="version = 1\n"))

    assert report.hard == ()
    assert [finding.category for finding in report.soft] == ["protected_overwrite"]


def test_manifest_deletion_stays_a_hard_protected_deletion() -> None:
    report = _report(_entry("package.json", status="D", content=None))

    assert [finding.category for finding in report.hard] == ["protected_deletion"]
    assert report.soft == ()
