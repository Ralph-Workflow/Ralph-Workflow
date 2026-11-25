from __future__ import annotations

from ralph.phases.commit_review import DiffEntry, TripwireThresholds, evaluate_tripwires

_BASE = frozenset(
    {
        "Cargo.toml",
        "Cargo.lock",
        "crates/core/Cargo.toml",
        "crates/core/src/lib.rs",
        "crates/legacy/Cargo.toml",
        "crates/legacy/src/lib.rs",
        *(f"crates/core/src/module_{index}.rs" for index in range(20)),
    }
)


def _deleted(*paths: str) -> list[DiffEntry]:
    return [_entry(path, base_tree_paths=_BASE) for path in paths]


def _entry(path: str, *, base_tree_paths: frozenset[str] | None = None) -> DiffEntry:
    return DiffEntry(
        path=path,
        status="D",
        old_mode="100644",
        new_mode="000000",
        size=0,
        is_binary=False,
        base_tree_paths=base_tree_paths,
    )


def test_removing_a_whole_nested_package_with_its_manifest_is_allowed() -> None:
    report = evaluate_tripwires(
        _deleted("crates/legacy/Cargo.toml", "crates/legacy/src/lib.rs"),
        len(_BASE),
        TripwireThresholds(),
    )

    assert report.hard == ()


def test_deleting_a_manifest_while_its_package_stays_is_still_rejected() -> None:
    report = evaluate_tripwires(
        _deleted("crates/core/Cargo.toml"), len(_BASE), TripwireThresholds()
    )

    assert [finding.category for finding in report.hard] == ["protected_deletion"]


def test_root_manifests_stay_protected_even_when_everything_is_deleted() -> None:
    report = evaluate_tripwires(
        _deleted("Cargo.toml", "Cargo.lock"), len(_BASE), TripwireThresholds()
    )

    assert {finding.path for finding in report.hard} >= {"Cargo.toml", "Cargo.lock"}


def test_a_plan_approval_alone_never_sanctions_a_live_package_manifest_deletion() -> None:
    report = evaluate_tripwires(
        _deleted("crates/core/Cargo.toml"),
        len(_BASE),
        TripwireThresholds(),
        approved_deletions=frozenset({"crates/core/Cargo.toml"}),
    )

    assert [finding.category for finding in report.hard] == ["protected_deletion"]


def test_without_base_tree_membership_a_nested_manifest_deletion_stays_rejected() -> None:
    report = evaluate_tripwires([_entry("crates/legacy/Cargo.toml")], 10, TripwireThresholds())

    assert [finding.category for finding in report.hard] == ["protected_deletion"]
