"""Unit tests for Ralph logging extensions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from ralph.logging import configure_logging

if TYPE_CHECKING:
    from pathlib import Path


ROTATED_FILE_COUNT_MINIMUM = 2
_SUCCESS_LEVEL_NO = 25
_MILESTONE_LEVEL_NO = 35


def test_configure_logging_creates_per_run_directory_and_structured_log(tmp_path: Path) -> None:
    """Configuring file logging should create a run-scoped directory and JSON log."""
    session = configure_logging(
        verbosity=2,
        log_directory=tmp_path,
        run_id="run-123",
        structured=True,
    )

    session.logger.bind(component="pipeline").info("pipeline started")

    assert session.paths.run_directory == tmp_path / "run-123"
    text_log_path = session.paths.text_log_path
    assert text_log_path is not None
    assert text_log_path.exists()
    structured_log_path = session.paths.structured_log_path
    assert structured_log_path is not None
    assert structured_log_path.exists()

    structured_lines = structured_log_path.read_text().strip().splitlines()
    payload = json.loads(structured_lines[-1])

    assert payload["record"]["message"] == "pipeline started"
    assert payload["record"]["extra"]["run_id"] == "run-123"
    assert payload["record"]["extra"]["component"] == "pipeline"


def test_configure_logging_rotates_run_log_files(tmp_path: Path) -> None:
    """Small rotation thresholds should produce multiple files in the run directory."""
    session = configure_logging(
        verbosity=2,
        log_directory=tmp_path,
        run_id="run-rotate",
        structured=False,
        rotation="400 B",
    )

    for index in range(40):
        session.logger.info(f"message {index} {'x' * 80}")

    run_directory = session.paths.run_directory
    assert run_directory is not None
    log_files = list(run_directory.glob("ralph*.log*"))
    assert len(log_files) >= ROTATED_FILE_COUNT_MINIMUM


def test_ralph_logger_emits_structured_phase_events(tmp_path: Path) -> None:
    """Structured logs should preserve event metadata from RalphLogger helpers."""
    session = configure_logging(
        verbosity=2,
        log_directory=tmp_path,
        run_id="run-events",
        structured=True,
    )

    session.ralph.phase_start("planning", "planner")

    structured_log_path = session.paths.structured_log_path
    assert structured_log_path is not None
    structured_lines = structured_log_path.read_text().strip().splitlines()
    payload = json.loads(structured_lines[-1])
    extra = payload["record"]["extra"]

    assert extra["event"] == "phase_start"
    assert extra["phase"] == "planning"
    assert extra["drain"] == "planner"


def test_configure_logging_registers_success_level() -> None:
    configure_logging()
    lvl = logger.level("SUCCESS")
    assert lvl.no == _SUCCESS_LEVEL_NO


def test_configure_logging_registers_milestone_level() -> None:
    configure_logging()
    lvl = logger.level("MILESTONE")
    assert lvl.no == _MILESTONE_LEVEL_NO


def test_success_and_milestone_methods_on_ralph_logger() -> None:
    session = configure_logging(verbosity=2)
    session.ralph.success("test success message")
    session.ralph.milestone("test milestone message")
