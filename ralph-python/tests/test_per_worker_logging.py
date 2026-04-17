from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from ralph.logging import WorkerSinkHandle, bind_worker_sink, remove_worker_sink


@pytest.fixture(autouse=True)
def _reset_loguru(tmp_path: Path) -> None:
    logger.remove()
    yield
    logger.remove()


def test_worker_log_files_created(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    run_id = "run-001"

    handles = [
        bind_worker_sink("unit-a", log_dir, run_id=run_id),
        bind_worker_sink("unit-b", log_dir, run_id=run_id),
        bind_worker_sink("unit-c", log_dir, run_id=run_id),
    ]

    logger.bind(unit_id="unit-a").info("message from unit-a")
    logger.bind(unit_id="unit-b").info("message from unit-b")
    logger.bind(unit_id="unit-c").info("message from unit-c")

    for handle in handles:
        logger.remove(handle.sink_id)

    workers_dir = log_dir / run_id / "workers"
    assert (workers_dir / "unit-unit-a.log").exists()
    assert (workers_dir / "unit-unit-b.log").exists()
    assert (workers_dir / "unit-unit-c.log").exists()


def test_no_cross_contamination(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    run_id = "run-002"

    handle_a = bind_worker_sink("unit-a", log_dir, run_id=run_id)
    handle_b = bind_worker_sink("unit-b", log_dir, run_id=run_id)

    logger.bind(unit_id="unit-a").info("unit-a-exclusive-message")
    logger.bind(unit_id="unit-b").info("unit-b-exclusive-message")

    logger.remove(handle_a.sink_id)
    logger.remove(handle_b.sink_id)

    workers_dir = log_dir / run_id / "workers"
    content_a = (workers_dir / "unit-unit-a.log").read_text()
    content_b = (workers_dir / "unit-unit-b.log").read_text()

    assert "unit-a-exclusive-message" in content_a
    assert "unit-a-exclusive-message" not in content_b
    assert "unit-b-exclusive-message" in content_b
    assert "unit-b-exclusive-message" not in content_a


def test_sink_removed_after_call(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    run_id = "run-003"

    handle = bind_worker_sink("unit-x", log_dir, run_id=run_id)
    logger.bind(unit_id="unit-x").info("before-removal-message")

    remove_worker_sink(handle)

    logger.bind(unit_id="unit-x").info("after-removal-message")

    workers_dir = log_dir / run_id / "workers"
    content = (workers_dir / "unit-unit-x.log").read_text()

    assert "before-removal-message" in content
    assert "after-removal-message" not in content


def test_log_path_structure(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    run_id = "run-004"

    handle = bind_worker_sink("worker-42", log_dir, run_id=run_id)
    logger.remove(handle.sink_id)

    expected = log_dir / run_id / "workers" / "unit-worker-42.log"
    assert handle.log_path == expected


def test_worker_sink_handle_is_dataclass(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    handle = bind_worker_sink("unit-z", log_dir, run_id="run-005")
    logger.remove(handle.sink_id)

    assert isinstance(handle, WorkerSinkHandle)
    assert isinstance(handle.sink_id, int)
    assert isinstance(handle.log_path, Path)


def test_default_run_id(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    handle = bind_worker_sink("unit-d", log_dir)
    logger.remove(handle.sink_id)

    expected = log_dir / "default" / "workers" / "unit-unit-d.log"
    assert handle.log_path == expected
