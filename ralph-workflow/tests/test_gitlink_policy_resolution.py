from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.gitlink_policy_config import GitlinkPolicyConfig, GitlinkPreference
from ralph.git.git_run_result import GitRunResult
from ralph.pipeline.conflict_resolution import deterministic_resolution
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.sight import ConflictKind

_BASE_SHA = "1" * 40
_OURS_SHA = "2" * 40
_THEIRS_SHA = "3" * 40


def _gitlink_stages(base: str, ours: str, theirs: str) -> dict[int, tuple[str, str]]:
    return {
        1: ("160000", base),
        2: ("160000", ours),
        3: ("160000", theirs),
    }


def _record_calls(
    monkeypatch: pytest.MonkeyPatch,
    stages: dict[str, dict[int, tuple[str, str]]],
) -> tuple[list[tuple[str, tuple[str, ...]]], list[tuple[object, ...]]]:
    calls: list[tuple[str, tuple[str, ...]]] = []
    records: list[tuple[object, ...]] = []

    def fake_run_git(
        args: tuple[str, ...],
        *,
        cwd: Path,
        label: str,
        options: object = None,
        call_class: object = None,
    ) -> GitRunResult:
        calls.append((label, tuple(args)))
        return GitRunResult(args=tuple(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        deterministic_resolution,
        "conflict_stage_entries",
        lambda _root, _paths: stages,
    )
    monkeypatch.setattr(
        deterministic_resolution,
        "run_git",
        fake_run_git,
    )
    monkeypatch.setattr(
        deterministic_resolution.logger,
        "info",
        lambda _message, *args: records.append(args),
    )
    return calls, records


def _gitlink_review(records: list[tuple[object, ...]]) -> tuple[object, ...]:
    for args in records:
        if len(args) == 8 and args[0] == "vendor":
            return args
    raise AssertionError(f"no gitlink review record was logged: {records}")


def _staged_gitlink_sha(calls: list[tuple[str, tuple[str, ...]]]) -> str:
    for label, args in calls:
        if not label.endswith("-readd"):
            continue
        for arg in args:
            if arg.startswith("160000,"):
                return arg.split(",")[1]
    raise AssertionError(f"no gitlink was staged: {calls}")


def test_default_gitlink_policy_completes_in_place_and_records_review(
    monkeypatch,
) -> None:
    calls, records = _record_calls(
        monkeypatch,
        {"vendor": _gitlink_stages(_BASE_SHA, _OURS_SHA, _THEIRS_SHA)},
    )

    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("vendor",),
        sha="stop",
        subject="gitlink",
    )
    assert deterministic_resolution.try_deterministic_resolution(Path("/repo"), stop)
    assert any(
        label == "git-gitlink-resolve-readd"
        and args[-1] == f"160000,{_OURS_SHA},vendor"
        for label, args in calls
    )
    assert records == [
        (
            "vendor",
            GitlinkPreference.VENDOR_IMMUTABLE,
            _BASE_SHA,
            _OURS_SHA,
            _THEIRS_SHA,
            "ours",
            _OURS_SHA,
            ("unchanged-from-base", "ours", "theirs"),
        )
    ]
    assert all("agent" not in label for label, _args in calls)


def test_configured_theirs_policy_selects_theirs() -> None:
    config = GitlinkPolicyConfig(pointer_preference="theirs")
    assert config.pointer_preference is GitlinkPreference.THEIRS


def test_completion_in_place_resolves_gitlink_by_configured_policy_side(
    monkeypatch,
) -> None:
    calls, records = _record_calls(
        monkeypatch,
        {"vendor": _gitlink_stages(_BASE_SHA, _OURS_SHA, _THEIRS_SHA)},
    )
    kinds = {
        "vendor": ConflictKind.GITLINK,
        "notes.txt": ConflictKind.TEXT,
    }

    completed = deterministic_resolution.resolve_completion_in_place(
        Path("/repo"),
        ("vendor", "notes.txt"),
        kinds,
        gitlink_policy=GitlinkPolicyConfig(pointer_preference="theirs"),
    )

    assert completed == ("vendor",)
    assert _staged_gitlink_sha(calls) == _THEIRS_SHA
    assert all(_OURS_SHA not in args for _label, args in calls)

    review = _gitlink_review(records)
    assert review[1] is GitlinkPreference.THEIRS
    assert review[2] == _BASE_SHA
    assert review[3] == _OURS_SHA
    assert review[4] == _THEIRS_SHA
    assert review[5] == "theirs"
    assert review[6] == _THEIRS_SHA
    assert review[7] == ("theirs", "ours")


def test_completion_in_place_default_policy_prefers_unchanged_side_not_ours(
    monkeypatch,
) -> None:
    calls, records = _record_calls(
        monkeypatch,
        {"vendor": _gitlink_stages(_BASE_SHA, _OURS_SHA, _BASE_SHA)},
    )
    kinds = {
        "vendor": ConflictKind.GITLINK,
        "notes.txt": ConflictKind.TEXT,
    }

    completed = deterministic_resolution.resolve_completion_in_place(
        Path("/repo"),
        ("vendor", "notes.txt"),
        kinds,
    )

    assert completed == ("vendor",)
    assert _OURS_SHA != _BASE_SHA
    assert _staged_gitlink_sha(calls) == _BASE_SHA
    assert all(_OURS_SHA not in args for _label, args in calls)

    review = _gitlink_review(records)
    assert review[1] is GitlinkPreference.VENDOR_IMMUTABLE
    assert review[2] == _BASE_SHA
    assert review[3] == _OURS_SHA
    assert review[4] == _BASE_SHA
    assert review[5] == "theirs"
    assert review[6] == _BASE_SHA
    assert review[7] == ("unchanged-from-base", "ours", "theirs")
