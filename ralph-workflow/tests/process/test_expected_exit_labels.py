from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from loguru import Message

from ralph.git.merge import is_ancestor
from ralph.git.subprocess_runner import run_git
from ralph.process.manager import (
    ProcessEvent,
    ProcessRecord,
    ProcessStatus,
    SpawnOptions,
    get_process_manager,
    loguru_event_listener,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_TARGET_LABELS: tuple[str, ...] = (
    "git-symbolic-ref-head",
    "git-is-ancestor",
    "check_merge_head",
    "integration-observation-head",
)


def _record_sink(target: list[str]) -> Callable[[Message], None]:
    def _sink(message: Message) -> None:
        target.append(str(message))

    return _sink


def _capture_emitted_logs(
    label: str,
    *,
    returncode: int | None,
    status: ProcessStatus,
    failure_message: str | None = None,
) -> list[str]:
    records: list[str] = []
    sink_id = logger.add(
        _record_sink(records),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        record = ProcessRecord(
            pid=9999,
            pgid=9999,
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


@pytest.mark.parametrize("label", _TARGET_LABELS)
def test_benign_probes_rc_one_emit_debug_and_no_warning(label: str) -> None:
    records = _capture_emitted_logs(
        label,
        returncode=1,
        status=ProcessStatus.EXITED,
    )
    assert len(records) == 1
    assert records[0].startswith("DEBUG:")
    assert "(expected answer)" in records[0]


@pytest.mark.parametrize("label", _TARGET_LABELS)
@pytest.mark.parametrize("returncode", [2, 128])
def test_probes_rc_two_and_above_emit_warning_and_no_debug(label: str, returncode: int) -> None:
    records = _capture_emitted_logs(
        label,
        returncode=returncode,
        status=ProcessStatus.EXITED,
    )
    assert any(line.startswith("WARNING:") for line in records)
    assert not any(line.startswith("DEBUG:") for line in records)


@pytest.mark.parametrize("label", _TARGET_LABELS)
def test_probe_spawn_failure_emits_error_and_no_debug(label: str) -> None:
    records = _capture_emitted_logs(
        label,
        returncode=None,
        status=ProcessStatus.FAILED,
        failure_message="No such file or directory",
    )
    assert len(records) == 1
    assert records[0].startswith("ERROR:")


def test_is_ancestor_rc_one_routes_through_severity_table_as_debug(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)

    (repo / "f.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "c1"], cwd=repo, check=True, capture_output=True)
    sha_c1 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    subprocess.run(["git", "checkout", "-b", "b2"], cwd=repo, check=True, capture_output=True)
    (repo / "f.txt").write_text("2", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "c2"], cwd=repo, check=True, capture_output=True)
    sha_c2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    records: list[str] = []
    sink_id = logger.add(
        _record_sink(records),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        ancestor_verdict = is_ancestor(repo, sha_c2, sha_c1)
    finally:
        logger.remove(sink_id)

    assert ancestor_verdict is False
    assert any(
        line.startswith("DEBUG:") and "git-is-ancestor" in line and "(expected answer)" in line
        for line in records
    )
    assert not any(line.startswith("WARNING:") for line in records)
    assert not any(line.startswith("ERROR:") for line in records)


def test_git_symbolic_ref_head_detached_head_rc_one_emits_debug_and_no_warning(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    (repo / "f.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "c1"], cwd=repo, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", sha], cwd=repo, check=True, capture_output=True)

    records: list[str] = []
    sink_id = logger.add(
        _record_sink(records),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        res = run_git(("symbolic-ref", "--quiet", "HEAD"), cwd=repo, label="git-symbolic-ref-head")
    finally:
        logger.remove(sink_id)

    assert res.returncode == 1
    assert any(
        line.startswith("DEBUG:")
        and "git-symbolic-ref-head" in line
        and "(expected answer)" in line
        for line in records
    )
    assert not any(line.startswith("WARNING:") for line in records)
    assert not any(line.startswith("ERROR:") for line in records)


def test_check_merge_head_absent_rc_one_emits_debug_and_no_warning(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    records: list[str] = []
    sink_id = logger.add(
        _record_sink(records),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        res = run_git(
            ("rev-parse", "--verify", "--quiet", "MERGE_HEAD"), cwd=repo, label="check_merge_head"
        )
    finally:
        logger.remove(sink_id)

    assert res.returncode == 1
    assert any(
        line.startswith("DEBUG:") and "check_merge_head" in line and "(expected answer)" in line
        for line in records
    )
    assert not any(line.startswith("WARNING:") for line in records)
    assert not any(line.startswith("ERROR:") for line in records)


def test_integration_observation_head_absent_ref_rc_one_emits_debug_and_no_warning(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    records: list[str] = []
    sink_id = logger.add(
        _record_sink(records),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        res = run_git(
            ("rev-parse", "--verify", "--quiet", "refs/heads/missing"),
            cwd=repo,
            label="integration-observation-head",
        )
    finally:
        logger.remove(sink_id)

    assert res.returncode == 1
    assert any(
        line.startswith("DEBUG:")
        and "integration-observation-head" in line
        and "(expected answer)" in line
        for line in records
    )
    assert not any(line.startswith("WARNING:") for line in records)
    assert not any(line.startswith("ERROR:") for line in records)


def test_probe_real_failure_rc_two_emits_warning(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    records: list[str] = []
    sink_id = logger.add(
        _record_sink(records),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        res = run_git(("--invalid-git-option-probe-test",), cwd=repo, label="git-is-ancestor")
    finally:
        logger.remove(sink_id)

    assert res.returncode >= 2
    assert any(line.startswith("WARNING:") and "git-is-ancestor" in line for line in records)
    assert not any(line.startswith("DEBUG:") for line in records)


def test_probe_spawn_failure_emits_error() -> None:
    pm = get_process_manager()
    records: list[str] = []
    sink_id = logger.add(
        _record_sink(records),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        with pytest.raises(FileNotFoundError):
            pm.spawn(
                ["/nonexistent-binary-probe-test-9999"], SpawnOptions(label="check_merge_head")
            )
    finally:
        logger.remove(sink_id)

    assert any(line.startswith("ERROR:") and "check_merge_head" in line for line in records)
    assert not any(line.startswith("DEBUG:") for line in records)
    assert not any(line.startswith("WARNING:") for line in records)
