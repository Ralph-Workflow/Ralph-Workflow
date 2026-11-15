from __future__ import annotations

from datetime import UTC, datetime

import pytest
from loguru import logger

from ralph.process.manager import (
    ProcessEvent,
    ProcessRecord,
    ProcessStatus,
    loguru_event_listener,
)
from ralph.process.manager._process_manager_runtime import expected_exit_codes

_HEAD_SYMBOLIC_REF_LABELS: tuple[str, ...] = (
    "git-symbolic-ref-head",
    "git-symbolic-ref",
    "symbolic-ref",
    "commit:head-symbolic-ref",
    "recovery:head-symbolic-ref",
    "integration-observation-head",
)
_IS_ANCESTOR_LABELS: tuple[str, ...] = (
    "git-is-ancestor",
    "is-ancestor",
    "git-rebase:merge-base",
    "integration_diagnostic:check_ancestor",
)
_CHECK_MERGE_HEAD_LABELS: tuple[str, ...] = (
    "check_merge_head",
    "check-merge-head",
    "integration_diagnostic:check_merge_head",
)
BENIGN_PROBE_LABELS: tuple[str, ...] = (
    *_HEAD_SYMBOLIC_REF_LABELS,
    *_IS_ANCESTOR_LABELS,
    *_CHECK_MERGE_HEAD_LABELS,
)


def _capture_emitted_logs(
    label: str,
    *,
    returncode: int | None,
    status: ProcessStatus = ProcessStatus.EXITED,
    failure_message: str | None = None,
) -> list[str]:
    records: list[str] = []
    sink_id = logger.add(
        lambda msg: records.append(str(msg)),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        record = ProcessRecord(
            pid=7777,
            pgid=7777,
            command=("git", "probe"),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=status,
            returncode=returncode,
            label=label,
            failure_message=failure_message,
        )
        event = ProcessEvent(
            record=record,
            previous_status=ProcessStatus.RUNNING,
            new_status=status,
            timestamp=datetime.now(tz=UTC),
        )
        loguru_event_listener(event)
    finally:
        logger.remove(sink_id)
    return records


@pytest.mark.parametrize("label", BENIGN_PROBE_LABELS)
def test_benign_probes_rc_one_emit_debug_expected_answer(label: str) -> None:
    records = _capture_emitted_logs(label, returncode=1)
    assert len(records) == 1
    assert records[0].startswith("DEBUG:")
    assert "(expected answer)" in records[0]


@pytest.mark.parametrize("label", BENIGN_PROBE_LABELS)
@pytest.mark.parametrize("returncode", [2, 128])
def test_benign_probes_rc_two_and_above_emit_warning(label: str, returncode: int) -> None:
    records = _capture_emitted_logs(label, returncode=returncode)
    assert len(records) == 1
    assert records[0].startswith("WARNING:")
    assert "(expected answer)" not in records[0]


@pytest.mark.parametrize("label", BENIGN_PROBE_LABELS)
def test_benign_probes_spawn_failure_does_not_emit_debug(label: str) -> None:
    records = _capture_emitted_logs(
        label,
        returncode=None,
        status=ProcessStatus.FAILED,
        failure_message="No such file or directory",
    )
    assert len(records) == 1
    assert records[0].startswith("ERROR:")
    assert "DEBUG" not in records[0]


def test_unregistered_probe_label_rc_one_emits_warning() -> None:
    records = _capture_emitted_logs("unknown-probe-label", returncode=1)
    assert len(records) == 1
    assert records[0].startswith("WARNING:")


def test_expected_exit_codes_lookup() -> None:
    assert 1 in expected_exit_codes("check_merge_head")
    assert 1 in expected_exit_codes("check-merge-head")
    assert 1 in expected_exit_codes("is-ancestor")
    assert 1 in expected_exit_codes("git-is-ancestor")
    assert 1 in expected_exit_codes("git-symbolic-ref-head")
    assert 1 in expected_exit_codes("symbolic-ref")
    assert 1 in expected_exit_codes("git-symbolic-ref")
    assert expected_exit_codes("unknown") == frozenset()
