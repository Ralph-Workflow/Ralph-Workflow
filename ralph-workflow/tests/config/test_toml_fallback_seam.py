from __future__ import annotations

import importlib
import stat
import tomllib
from typing import TYPE_CHECKING

import pytest
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_toml_fallback = importlib.import_module("ralph.config._toml_fallback")
load_optional_toml = _toml_fallback.load_optional_toml
validate_or_default = _toml_fallback.validate_or_default
reset_fallback_warning_registry = _toml_fallback.reset_fallback_warning_registry


@pytest.fixture(autouse=True)
def _clear_registry_before_and_after() -> Generator[None, None, None]:
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


def _capture_warnings() -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    return records, sink_id


def test_load_optional_toml_valid_file(tmp_path: Path) -> None:
    toml_file = tmp_path / "valid.toml"
    content = 'agent_name = "test_agent"\nversion = 1\n'
    toml_file.write_text(content, encoding="utf-8")
    before_bytes = toml_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        data = load_optional_toml(toml_file, kind="test")
    finally:
        logger.remove(sink_id)

    assert data == {"agent_name": "test_agent", "version": 1}
    assert records == []
    assert toml_file.read_bytes() == before_bytes


def test_load_optional_toml_missing_file_silent_default(tmp_path: Path) -> None:
    missing_file = tmp_path / "does_not_exist.toml"

    records, sink_id = _capture_warnings()
    try:
        data = load_optional_toml(missing_file, kind="test")
        with_factory = load_optional_toml(
            missing_file, kind="test", default_factory=lambda: {"fallback": 42}
        )
        strict_result = load_optional_toml(missing_file, kind="test", strict=True)
    finally:
        logger.remove(sink_id)

    assert data == {}
    assert with_factory == {"fallback": 42}
    assert strict_result == {}
    assert records == []


def test_load_optional_toml_invalid_syntax_fallback(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.toml"
    bad_file.write_text("a = [\nkey = 1\n", encoding="utf-8")
    before_bytes = bad_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        data = load_optional_toml(bad_file, kind="agents", strict=False)
    finally:
        logger.remove(sink_id)

    assert data == {}
    assert len(records) == 1
    msg = records[0]
    assert str(bad_file) in msg
    assert "line" in msg.lower()
    assert "using built-in default" in msg
    assert "ralph --check-config" in msg
    assert bad_file.read_bytes() == before_bytes


def test_load_optional_toml_invalid_syntax_strict_raises(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.toml"
    bad_file.write_text("invalid_syntax = [unclosed", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_optional_toml(bad_file, kind="agents", strict=True)


def test_load_optional_toml_bad_encoding_fallback(tmp_path: Path) -> None:
    bad_encoding_file = tmp_path / "bad_encoding.toml"
    bad_encoding_file.write_bytes(b"\xff\xfe\x00\x01\x02")
    before_bytes = bad_encoding_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        data = load_optional_toml(bad_encoding_file, kind="policy", strict=False)
    finally:
        logger.remove(sink_id)

    assert data == {}
    assert len(records) == 1
    msg = records[0]
    assert str(bad_encoding_file) in msg
    assert "using built-in default" in msg
    assert "ralph --check-config" in msg
    assert bad_encoding_file.read_bytes() == before_bytes


def test_load_optional_toml_bad_encoding_strict_raises(tmp_path: Path) -> None:
    bad_encoding_file = tmp_path / "bad_encoding.toml"
    bad_encoding_file.write_bytes(b"\xff\xfe\x00\x01\x02")

    with pytest.raises(UnicodeDecodeError):
        load_optional_toml(bad_encoding_file, kind="policy", strict=True)


def test_load_optional_toml_permission_error_fallback(tmp_path: Path) -> None:
    unreadable_file = tmp_path / "unreadable.toml"
    unreadable_file.write_text("a = 1\n", encoding="utf-8")
    unreadable_file.chmod(0o000)

    try:
        records, sink_id = _capture_warnings()
        try:
            data = load_optional_toml(unreadable_file, kind="mcp", strict=False)
        finally:
            logger.remove(sink_id)

        assert data == {}
        assert len(records) == 1
        msg = records[0]
        assert str(unreadable_file) in msg
        assert "using built-in default" in msg
        assert "ralph --check-config" in msg

        with pytest.raises(PermissionError):
            load_optional_toml(unreadable_file, kind="mcp", strict=True)
    finally:
        unreadable_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_load_optional_toml_deduplication_and_change_detection(tmp_path: Path) -> None:
    file_path = tmp_path / "dedup_test.toml"
    file_path.write_text("bad = [1, \n", encoding="utf-8")

    records, sink_id = _capture_warnings()
    try:
        load_optional_toml(file_path, kind="agents", strict=False)
        assert len(records) == 1

        load_optional_toml(file_path, kind="agents", strict=False)
        assert len(records) == 1

        file_path.write_text("another_bad = {unterminated", encoding="utf-8")
        load_optional_toml(file_path, kind="agents", strict=False)
        assert len(records) == 2

        load_optional_toml(file_path, kind="agents", strict=False)
        assert len(records) == 2
    finally:
        logger.remove(sink_id)


def test_reset_fallback_warning_registry(tmp_path: Path) -> None:
    file_path = tmp_path / "reset_test.toml"
    file_path.write_text("broken = [", encoding="utf-8")

    records, sink_id = _capture_warnings()
    try:
        load_optional_toml(file_path, kind="agents", strict=False)
        assert len(records) == 1

        load_optional_toml(file_path, kind="agents", strict=False)
        assert len(records) == 1

        reset_fallback_warning_registry()

        load_optional_toml(file_path, kind="agents", strict=False)
        assert len(records) == 2
    finally:
        logger.remove(sink_id)


def test_load_optional_toml_custom_default_factory(tmp_path: Path) -> None:
    file_path = tmp_path / "factory_test.toml"
    file_path.write_text("bad_syntax", encoding="utf-8")

    records, sink_id = _capture_warnings()
    try:
        result = load_optional_toml(
            file_path,
            kind="custom",
            strict=False,
            default_factory=lambda: {"custom": "default_value"},
        )
    finally:
        logger.remove(sink_id)

    assert result == {"custom": "default_value"}
    assert len(records) == 1


def test_validate_or_default_valid_data(tmp_path: Path) -> None:
    file_path = tmp_path / "valid_data.toml"
    data = {"count": 10}

    records, sink_id = _capture_warnings()
    try:
        result = validate_or_default(
            file_path,
            data,
            validator=lambda d: d["count"] * 2,
            default=0,
            kind="test",
        )
    finally:
        logger.remove(sink_id)

    assert result == 20
    assert records == []


def test_validate_or_default_schema_invalid_fallback(tmp_path: Path) -> None:
    file_path = tmp_path / "schema_invalid.toml"
    file_path.write_text("count = -5\n", encoding="utf-8")

    def _validator(data: dict[str, int]) -> dict[str, int]:
        if data.get("count", 0) < 0:
            raise ValueError("count must be non-negative")
        return data

    records, sink_id = _capture_warnings()
    try:
        result = validate_or_default(
            file_path,
            {"count": -5},
            validator=_validator,
            default={"count": 0},
            kind="agents",
            strict=False,
        )
        assert result == {"count": 0}
        assert len(records) == 1
        msg = records[0]
        assert str(file_path) in msg
        assert "count must be non-negative" in msg
        assert "using built-in default" in msg
        assert "ralph --check-config" in msg

        result2 = validate_or_default(
            file_path,
            {"count": -5},
            validator=_validator,
            default={"count": 0},
            kind="agents",
            strict=False,
        )
        assert result2 == {"count": 0}
        assert len(records) == 1

        file_path.write_text("count = -99\n", encoding="utf-8")
        result3 = validate_or_default(
            file_path,
            {"count": -99},
            validator=_validator,
            default={"count": 0},
            kind="agents",
            strict=False,
        )
        assert result3 == {"count": 0}
        assert len(records) == 2
    finally:
        logger.remove(sink_id)


def test_validate_or_default_schema_invalid_strict_raises(tmp_path: Path) -> None:
    file_path = tmp_path / "schema_invalid.toml"

    def _failing_validator(_data: object) -> object:
        raise ValueError("schema constraint violated")

    with pytest.raises(ValueError, match="schema constraint violated"):
        validate_or_default(
            file_path,
            {},
            validator=_failing_validator,
            default={},
            kind="mcp",
            strict=True,
        )


def test_validate_or_default_handles_system_exit(tmp_path: Path) -> None:
    file_path = tmp_path / "system_exit.toml"

    def _exit_validator(_data: object) -> object:
        raise SystemExit(1)

    records, sink_id = _capture_warnings()
    try:
        result = validate_or_default(
            file_path,
            {},
            validator=_exit_validator,
            default={"fallback": True},
            kind="pipeline",
            strict=False,
        )
    finally:
        logger.remove(sink_id)

    assert result == {"fallback": True}
    assert len(records) == 1
    assert "using built-in default" in records[0]

    with pytest.raises(SystemExit):
        validate_or_default(
            file_path,
            {},
            validator=_exit_validator,
            default={"fallback": True},
            kind="pipeline",
            strict=True,
        )
