from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config import (
    emit_config_failure_warning,
    load_optional_toml,
    reset_fallback_warning_registry,
)
from ralph.process.manager import ProcessManager, SpawnOptions

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _clean_fallback_registry() -> Generator[None, None, None]:
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


@pytest.fixture()
def warning_records() -> Generator[list[str], None, None]:
    records: list[str] = []

    def _sink(message: object) -> None:
        records.append(str(message))

    sink_id = logger.add(_sink, level="WARNING")
    try:
        yield records
    finally:
        logger.remove(sink_id)


def test_emit_config_failure_warning_single_error_warns_once_and_repeat_is_silent(
    tmp_path: Path,
    warning_records: list[str],
) -> None:
    cfg = tmp_path / "broken.toml"
    cfg.write_text("invalid syntax\n", encoding="utf-8")
    exc = ValueError("bad syntax")

    first_emitted = emit_config_failure_warning(
        cfg,
        kind="agents",
        error_detail="bad syntax",
        behaviour="using built-in default",
        fix_hint="Fix: run ralph --check-config.",
        exc=exc,
    )
    assert first_emitted is True
    assert len(warning_records) == 1
    assert str(cfg) in warning_records[0]
    assert "agents" in warning_records[0]

    second_emitted = emit_config_failure_warning(
        cfg,
        kind="agents",
        error_detail="bad syntax",
        behaviour="using built-in default",
        fix_hint="Fix: run ralph --check-config.",
        exc=exc,
    )
    assert second_emitted is False
    assert len(warning_records) == 1

    third_emitted = emit_config_failure_warning(
        cfg,
        kind="agents",
        error_detail="bad syntax",
        behaviour="using built-in default",
        fix_hint="Fix: run ralph --check-config.",
        exc=exc,
    )
    assert third_emitted is False
    assert len(warning_records) == 1


def test_emit_config_failure_warning_changed_error_or_file_warns_again(
    tmp_path: Path,
    warning_records: list[str],
) -> None:
    file_a = tmp_path / "cfg_a.toml"
    file_b = tmp_path / "cfg_b.toml"
    file_a.write_text("a = 1\n", encoding="utf-8")
    file_b.write_text("b = 2\n", encoding="utf-8")
    exc_a = ValueError("error on a")
    exc_b = ValueError("error on b")

    assert (
        emit_config_failure_warning(
            file_a,
            kind="agents",
            error_detail="error on a",
            behaviour="using built-in default",
            fix_hint="Fix: check a",
            exc=exc_a,
        )
        is True
    )
    assert len(warning_records) == 1

    assert (
        emit_config_failure_warning(
            file_b,
            kind="agents",
            error_detail="error on b",
            behaviour="using built-in default",
            fix_hint="Fix: check b",
            exc=exc_b,
        )
        is True
    )
    assert len(warning_records) == 2

    assert (
        emit_config_failure_warning(
            file_a,
            kind="agents",
            error_detail="error on a",
            behaviour="using built-in default",
            fix_hint="Fix: check a",
            exc=exc_a,
        )
        is False
    )
    assert (
        emit_config_failure_warning(
            file_b,
            kind="agents",
            error_detail="error on b",
            behaviour="using built-in default",
            fix_hint="Fix: check b",
            exc=exc_b,
        )
        is False
    )
    assert len(warning_records) == 2

    file_a.write_text("different_content = [[\n", encoding="utf-8")
    exc_a_changed = ValueError("changed error on a")
    assert (
        emit_config_failure_warning(
            file_a,
            kind="agents",
            error_detail="changed error on a",
            behaviour="using built-in default",
            fix_hint="Fix: check a",
            exc=exc_a_changed,
        )
        is True
    )
    assert len(warning_records) == 3

    assert (
        emit_config_failure_warning(
            file_a,
            kind="agents",
            error_detail="changed error on a",
            behaviour="using built-in default",
            fix_hint="Fix: check a",
            exc=exc_a_changed,
        )
        is False
    )
    assert len(warning_records) == 3

    missing_file = tmp_path / "nonexistent.toml"
    assert (
        emit_config_failure_warning(
            missing_file,
            kind="agents",
            error_detail="missing",
            behaviour="using built-in default",
            fix_hint="Fix: create file",
            exc=FileNotFoundError("missing"),
        )
        is True
    )
    assert len(warning_records) == 4

    assert (
        emit_config_failure_warning(
            missing_file,
            kind="agents",
            error_detail="missing",
            behaviour="using built-in default",
            fix_hint="Fix: create file",
            exc=FileNotFoundError("missing"),
        )
        is False
    )
    assert len(warning_records) == 4

    assert (
        emit_config_failure_warning(
            missing_file,
            kind="agents",
            error_detail="permission denied",
            behaviour="using built-in default",
            fix_hint="Fix: check permissions",
            exc=PermissionError("denied"),
        )
        is True
    )
    assert len(warning_records) == 5


def test_load_optional_toml_warning_frequency_and_repeat_suppression(
    tmp_path: Path,
    warning_records: list[str],
) -> None:
    bad_file = tmp_path / "broken_pipeline.toml"
    bad_file.write_text("[section\nunterminated = [1, 2,\n", encoding="utf-8")

    result1 = load_optional_toml(bad_file, kind="pipeline")
    assert result1 == {}
    assert len(warning_records) == 1
    assert str(bad_file) in warning_records[0]
    assert "pipeline" in warning_records[0]

    result2 = load_optional_toml(bad_file, kind="pipeline")
    assert result2 == {}
    assert len(warning_records) == 1

    result3 = load_optional_toml(bad_file, kind="pipeline")
    assert result3 == {}
    assert len(warning_records) == 1

    bad_file.write_text("[other_section\nunterminated = {\n", encoding="utf-8")
    result4 = load_optional_toml(bad_file, kind="pipeline")
    assert result4 == {}
    assert len(warning_records) == 2

    result5 = load_optional_toml(bad_file, kind="pipeline")
    assert result5 == {}
    assert len(warning_records) == 2

    other_bad_file = tmp_path / "other_broken.toml"
    other_bad_file.write_text("bad = [[[\n", encoding="utf-8")

    result6 = load_optional_toml(other_bad_file, kind="pipeline")
    assert result6 == {}
    assert len(warning_records) == 3

    result7 = load_optional_toml(other_bad_file, kind="pipeline")
    assert result7 == {}
    assert len(warning_records) == 3


def test_config_fallback_warning_registry_is_per_process(tmp_path: Path) -> None:
    bad_file = tmp_path / "process_test.toml"
    bad_file.write_text("broken = [[\n", encoding="utf-8")

    driver_code = (
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from loguru import logger\n"
        "from ralph.config import load_optional_toml\n"
        "records = []\n"
        'logger.add(lambda msg: records.append(str(msg)), level="WARNING")\n'
        "p = Path(sys.argv[1])\n"
        'load_optional_toml(p, kind="agents")\n'
        'load_optional_toml(p, kind="agents")\n'
        "print(len(records))\n"
    )

    pm1 = ProcessManager()
    proc1 = pm1.spawn(
        [sys.executable, "-c", driver_code, str(bad_file)],
        SpawnOptions(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ),
        ),
    )
    stdout1, stderr1 = proc1.communicate(timeout=10.0)
    assert proc1.returncode == 0, f"Process 1 failed: {stderr1.decode() if stderr1 else ''}"
    assert stdout1 is not None
    assert stdout1.decode().strip() == "1"

    pm2 = ProcessManager()
    proc2 = pm2.spawn(
        [sys.executable, "-c", driver_code, str(bad_file)],
        SpawnOptions(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ),
        ),
    )
    stdout2, stderr2 = proc2.communicate(timeout=10.0)
    assert proc2.returncode == 0, f"Process 2 failed: {stderr2.decode() if stderr2 else ''}"
    assert stdout2 is not None
    assert stdout2.decode().strip() == "1"


def test_fallback_warning_registry_capacity_bounded_and_evicts_oldest(
    tmp_path: Path,
) -> None:
    max_capacity = 256
    for i in range(max_capacity + 5):
        cfg = tmp_path / f"cfg_{i}.toml"
        cfg.write_text(f"val = {i}\n", encoding="utf-8")
        emitted = emit_config_failure_warning(
            cfg,
            kind="pipeline",
            error_detail=f"error {i}",
            behaviour="using built-in default",
            fix_hint="Fix: check",
            exc=ValueError(f"err {i}"),
        )
        assert emitted is True

    re_emitted_oldest = emit_config_failure_warning(
        tmp_path / "cfg_0.toml",
        kind="pipeline",
        error_detail="error 0",
        behaviour="using built-in default",
        fix_hint="Fix: check",
        exc=ValueError("err 0"),
    )
    assert re_emitted_oldest is True

    re_emitted_latest = emit_config_failure_warning(
        tmp_path / f"cfg_{max_capacity + 4}.toml",
        kind="pipeline",
        error_detail=f"error {max_capacity + 4}",
        behaviour="using built-in default",
        fix_hint="Fix: check",
        exc=ValueError(f"err {max_capacity + 4}"),
    )
    assert re_emitted_latest is False
