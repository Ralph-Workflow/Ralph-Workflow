from __future__ import annotations

from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import (
    attempt_stop_resolution,
    reset_stop_resolution_presentation,
    retained_round_is_new,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.key_unavailable import KeyUnavailable
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey
from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger


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
            launches.append("launched")
            or RebaseResolutionResult(
                False,
                "still conflicted",
                ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
        ),
    )


def test_unchanged_active_gate_never_launches_resolver(tmp_path: Path) -> None:
    key = ResolutionAttemptKey("stop", ("agent-a",), "config")
    record = _record(tmp_path, key)
    launches: list[str] = []

    result = attempt_stop_resolution("stop", _trigger(tmp_path, record, key, launches))

    assert isinstance(result, StopResolutionBlocked)
    assert "gate active" in result.reason
    assert "unchanged" in result.reason
    assert launches == []


def test_key_failure_is_typed_and_never_launches_or_reshapes(tmp_path: Path) -> None:
    record = _record(tmp_path)
    unavailable = KeyUnavailable("controller exploded")
    launches: list[str] = []

    result = attempt_stop_resolution("stop", _trigger(tmp_path, record, unavailable, launches))

    assert isinstance(result, StopResolutionBlocked)
    assert result.key is unavailable
    assert result.reason.endswith("controller exploded")
    assert launches == []
    assert read_record(tmp_path) == record


def test_public_boundary_accepts_facts_clock_and_launcher(tmp_path: Path) -> None:
    key = ResolutionAttemptKey("stop", ("agent-b",), "config")
    record = _record(tmp_path)
    launches: list[str] = []

    result = attempt_stop_resolution("stop", _trigger(tmp_path, record, key, launches))

    assert isinstance(result, RebaseResolutionResult)
    assert launches == ["launched"]
    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.resolution_gate_key == tuple(key)


def test_key_change_reopens_attempt_once(tmp_path: Path) -> None:
    old_key = ResolutionAttemptKey("stop", ("agent-a",), "config")
    new_key = ResolutionAttemptKey("stop", ("agent-a", "agent-b"), "config")
    record = _record(tmp_path, old_key)
    launches: list[str] = []

    first = attempt_stop_resolution("stop", _trigger(tmp_path, record, new_key, launches))
    persisted = read_record(tmp_path)
    assert isinstance(first, RebaseResolutionResult)
    assert launches == ["launched"]
    assert persisted is not None
    assert persisted.resolution_gate_key == tuple(new_key)

    second = attempt_stop_resolution("stop", _trigger(tmp_path, persisted, new_key, launches))
    assert isinstance(second, StopResolutionBlocked)
    assert launches == ["launched"]


def test_retained_round_is_new_and_reset_presentation() -> None:
    reset_stop_resolution_presentation()
    try:
        r1 = Path("/virtual/workspace_a")
        r2 = Path("/virtual/workspace_b")

        assert retained_round_is_new(r1, "sha-1") is True
        assert retained_round_is_new(r1, "sha-1") is False
        assert retained_round_is_new(r1, "sha-2") is True
        assert retained_round_is_new(r1, "sha-2") is False

        assert retained_round_is_new(r2, "sha-2") is True
        assert retained_round_is_new(r2, "sha-2") is False

        reset_stop_resolution_presentation()
        assert retained_round_is_new(r1, "sha-2") is True
    finally:
        reset_stop_resolution_presentation()


def test_retained_round_eviction_at_module_cap() -> None:
    reset_stop_resolution_presentation()
    try:
        paths = [Path(f"/virtual/workspace_{i}") for i in range(1024)]
        for i, path in enumerate(paths):
            assert retained_round_is_new(path, f"fp-{i}") is True

        assert retained_round_is_new(paths[-1], f"fp-{len(paths) - 1}") is False

        overflow_path = Path("/virtual/workspace_overflow")
        assert retained_round_is_new(overflow_path, "fp-overflow") is True

        assert retained_round_is_new(paths[-1], f"fp-{len(paths) - 1}") is False
        assert retained_round_is_new(paths[0], "fp-0") is True
    finally:
        reset_stop_resolution_presentation()
