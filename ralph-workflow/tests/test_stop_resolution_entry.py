from __future__ import annotations

from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import (
    StopResolutionTrigger,
    attempt_stop_resolution,
    reset_stop_resolution_presentation,
    retained_round_is_new,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.key_unavailable import KeyUnavailable
from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey
from ralph.pipeline.resolution_outcome import RetryLaterOutcome
from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked


def _record(root: Path, key: ResolutionAttemptKey | None = None) -> IntegrationRecord:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
        resolution_gate_key=None if key is None else tuple(key),
    )
    write_record(root, record)
    return record


def _trigger(
    root: Path,
    record: IntegrationRecord,
    key: ResolutionAttemptKey | KeyUnavailable,
    launches: list[str],
) -> StopResolutionTrigger:
    return StopResolutionTrigger(
        name="rebase_stop",
        root=root,
        target="main",
        resolver=object(),
        record=record,
        clock=FakeClock(start=12.0),
        key_provider=lambda: key,
        launcher=lambda: (
            launches.append("launched") or RetryLaterOutcome(on_change="conflict state changes")
        ),
    )


def test_unchanged_active_gate_never_launches_resolver(tmp_path: Path) -> None:
    key = ResolutionAttemptKey("stop", ("agent-a",), "config")
    launches: list[str] = []

    result = attempt_stop_resolution(
        "stop", _trigger(tmp_path, _record(tmp_path, key), key, launches)
    )

    assert isinstance(result, StopResolutionBlocked)
    assert "gate active" in result.reason
    assert "unchanged" in result.reason
    assert launches == []


def test_key_failure_is_typed_and_preserves_the_record(tmp_path: Path) -> None:
    record = _record(tmp_path)
    unavailable = KeyUnavailable("controller exploded")
    launches: list[str] = []

    result = attempt_stop_resolution("stop", _trigger(tmp_path, record, unavailable, launches))

    assert isinstance(result, StopResolutionBlocked)
    assert result.key is unavailable
    assert result.reason.endswith("controller exploded")
    assert launches == []
    assert read_record(tmp_path) == record


def test_public_entry_returns_the_sealed_outcome_and_persists_the_gate(tmp_path: Path) -> None:
    key = ResolutionAttemptKey("stop", ("agent-b",), "config")
    launches: list[str] = []

    result = attempt_stop_resolution("stop", _trigger(tmp_path, _record(tmp_path), key, launches))

    assert isinstance(result, RetryLaterOutcome)
    assert result.on_change == "conflict state changes"
    assert launches == ["launched"]
    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.resolution_gate_key == tuple(key)


def test_key_change_reopens_exactly_one_attempt(tmp_path: Path) -> None:
    old_key = ResolutionAttemptKey("stop", ("agent-a",), "config")
    new_key = ResolutionAttemptKey("stop", ("agent-a", "agent-b"), "config")
    launches: list[str] = []

    first = attempt_stop_resolution(
        "stop", _trigger(tmp_path, _record(tmp_path, old_key), new_key, launches)
    )
    persisted = read_record(tmp_path)

    assert isinstance(first, RetryLaterOutcome)
    assert persisted is not None
    assert persisted.resolution_gate_key == tuple(new_key)
    second = attempt_stop_resolution("stop", _trigger(tmp_path, persisted, new_key, launches))
    assert isinstance(second, StopResolutionBlocked)
    assert launches == ["launched"]


def test_retained_round_presentation_is_scoped_and_resettable() -> None:
    reset_stop_resolution_presentation()
    try:
        first = Path("/virtual/workspace_a")
        second = Path("/virtual/workspace_b")

        assert retained_round_is_new(first, "sha-1") is True
        assert retained_round_is_new(first, "sha-1") is False
        assert retained_round_is_new(first, "sha-2") is True
        assert retained_round_is_new(second, "sha-2") is True

        reset_stop_resolution_presentation()
        assert retained_round_is_new(first, "sha-2") is True
    finally:
        reset_stop_resolution_presentation()
