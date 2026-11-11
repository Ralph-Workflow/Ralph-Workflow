from __future__ import annotations

import stat
import textwrap
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config import reset_fallback_warning_registry
from ralph.config.mcp_loader import (
    McpConfigError,
    load_mcp_config,
)
from ralph.config.mcp_models import McpConfig
from ralph.config.web_service_configs import WebVisitConfig

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


class _FakeScope:
    def __init__(self, root: Path) -> None:
        self.local_config_path = root / ".agent" / "ralph-workflow.toml"

    def resolve_agent_file(self, filename: str) -> Path:
        return self.local_config_path.parent / filename


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


def test_global_mcp_invalid_toml_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    global_file = xdg / "ralph-workflow-mcp.toml"
    global_file.write_bytes(b"[invalid = }")
    initial_bytes = global_file.read_bytes()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=tmp_path / "nonexistent.toml")
    finally:
        logger.remove(sink_id)

    assert config == McpConfig()
    assert global_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(global_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_global_mcp_bad_encoding_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    global_file = xdg / "ralph-workflow-mcp.toml"
    global_file.write_bytes(b"\xff\xfe\x00\x01\x02")
    initial_bytes = global_file.read_bytes()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=tmp_path / "nonexistent.toml")
    finally:
        logger.remove(sink_id)

    assert config == McpConfig()
    assert global_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(global_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_global_mcp_permission_error_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    global_file = xdg / "ralph-workflow-mcp.toml"
    global_file.write_text("[media]\nenabled = true\n", encoding="utf-8")
    initial_bytes = global_file.read_bytes()
    global_file.chmod(0o000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    try:
        records, sink_id = _capture_warnings()
        try:
            config = load_mcp_config(config_path=tmp_path / "nonexistent.toml")
        finally:
            logger.remove(sink_id)

        assert config == McpConfig()
        assert len(records) == 1
        assert str(global_file) in records[0]
        assert "using built-in default" in records[0]
        assert "ralph --check-config" in records[0]
    finally:
        global_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert global_file.read_bytes() == initial_bytes


def test_global_mcp_schema_invalid_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    global_file = xdg / "ralph-workflow-mcp.toml"
    global_file.write_text(
        textwrap.dedent("""\
            [media]
            enabled = true
            max_inline_bytes = 0
        """),
        encoding="utf-8",
    )
    initial_bytes = global_file.read_bytes()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=tmp_path / "nonexistent.toml")
    finally:
        logger.remove(sink_id)

    assert config == McpConfig()
    assert config.media.max_inline_bytes == 5_242_880
    assert global_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(global_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_local_mcp_invalid_toml_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    local_file = tmp_path / "mcp.toml"
    local_file.write_bytes(b"[invalid = }")
    initial_bytes = local_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=local_file)
    finally:
        logger.remove(sink_id)

    assert config == McpConfig()
    assert local_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(local_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_local_mcp_bad_encoding_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    local_file = tmp_path / "mcp.toml"
    local_file.write_bytes(b"\xff\xfe\x00\x01\x02")
    initial_bytes = local_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=local_file)
    finally:
        logger.remove(sink_id)

    assert config == McpConfig()
    assert local_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(local_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_local_mcp_permission_error_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    local_file = tmp_path / "mcp.toml"
    local_file.write_text("[media]\nenabled = true\n", encoding="utf-8")
    initial_bytes = local_file.read_bytes()
    local_file.chmod(0o000)

    try:
        records, sink_id = _capture_warnings()
        try:
            config = load_mcp_config(config_path=local_file)
        finally:
            logger.remove(sink_id)

        assert config == McpConfig()
        assert len(records) == 1
        assert str(local_file) in records[0]
        assert "using built-in default" in records[0]
        assert "ralph --check-config" in records[0]
    finally:
        local_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert local_file.read_bytes() == initial_bytes


def test_local_mcp_schema_invalid_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    local_file = tmp_path / "mcp.toml"
    local_file.write_text(
        textwrap.dedent("""\
            [mcp_servers.ralph]
            name = "ralph"
            transport = "stdio"
            command = "npx"
        """),
        encoding="utf-8",
    )
    initial_bytes = local_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=local_file)
    finally:
        logger.remove(sink_id)

    assert config == McpConfig()
    assert config.mcp_servers == {}
    assert local_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(local_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_local_mcp_web_visit_schema_invalid_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    local_file = tmp_path / "mcp.toml"
    local_file.write_text(
        textwrap.dedent("""\
            [mcp_servers.test_server]
            name = "test_server"
            transport = "stdio"
            command = "echo"

            [web_visit]
            timeout_ms = -50
        """),
        encoding="utf-8",
    )
    initial_bytes = local_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=local_file)
    finally:
        logger.remove(sink_id)

    assert config.web_visit == WebVisitConfig()
    assert config.web_visit.timeout_ms == 15000
    assert config.mcp_servers == {}
    assert local_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(local_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_global_mcp_web_visit_schema_invalid_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    global_file = xdg / "ralph-workflow-mcp.toml"
    global_file.write_text(
        textwrap.dedent("""\
            [mcp_servers.test_server]
            name = "test_server"
            transport = "stdio"
            command = "echo"

            [web_visit]
            timeout_ms = -50
        """),
        encoding="utf-8",
    )
    initial_bytes = global_file.read_bytes()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(config_path=tmp_path / "nonexistent.toml")
    finally:
        logger.remove(sink_id)

    assert config.web_visit == WebVisitConfig()
    assert config.web_visit.timeout_ms == 15000
    assert config.mcp_servers == {}
    assert global_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(global_file) in records[0]
    assert "using built-in default" in records[0]
    assert "ralph --check-config" in records[0]


def test_local_mcp_workspace_scope_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    local_file = agent_dir / "mcp.toml"
    local_file.write_text("[invalid syntax =", encoding="utf-8")
    initial_bytes = local_file.read_bytes()

    scope = _FakeScope(tmp_path)
    records, sink_id = _capture_warnings()
    try:
        config = load_mcp_config(workspace_scope=scope)
    finally:
        logger.remove(sink_id)

    assert config == McpConfig()
    assert local_file.read_bytes() == initial_bytes
    assert len(records) == 1
    assert str(local_file) in records[0]
    assert "using built-in default" in records[0]


def test_bundled_default_invalid_toml_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_bundled = tmp_path / "bundled_bad.toml"
    bad_bundled.write_bytes(b"[invalid = }")
    monkeypatch.setattr(
        "ralph.config.mcp_loader.bundled_default_mcp_config_path",
        lambda: bad_bundled,
    )

    with pytest.raises(McpConfigError):
        load_mcp_config(strict_optional_files=False)


def test_bundled_default_bad_encoding_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_bundled = tmp_path / "bundled_bad.toml"
    bad_bundled.write_bytes(b"\xff\xfe\x00\x01\x02")
    monkeypatch.setattr(
        "ralph.config.mcp_loader.bundled_default_mcp_config_path",
        lambda: bad_bundled,
    )

    with pytest.raises(McpConfigError):
        load_mcp_config(strict_optional_files=False)


def test_bundled_default_permission_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_bundled = tmp_path / "bundled_bad.toml"
    bad_bundled.write_text("[media]\nenabled = true\n", encoding="utf-8")
    bad_bundled.chmod(0o000)
    monkeypatch.setattr(
        "ralph.config.mcp_loader.bundled_default_mcp_config_path",
        lambda: bad_bundled,
    )

    try:
        with pytest.raises(McpConfigError):
            load_mcp_config(strict_optional_files=False)
    finally:
        bad_bundled.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_bundled_default_schema_invalid_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_bundled = tmp_path / "bundled_bad.toml"
    bad_bundled.write_text(
        textwrap.dedent("""\
            [media]
            enabled = true
            max_inline_bytes = 0
        """),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ralph.config.mcp_loader.bundled_default_mcp_config_path",
        lambda: bad_bundled,
    )

    with pytest.raises(McpConfigError):
        load_mcp_config(strict_optional_files=False)


def test_strict_optional_files_true_re_raises_for_global_and_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    (xdg / "ralph-workflow-mcp.toml").write_bytes(b"[invalid = }")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    with pytest.raises(McpConfigError):
        load_mcp_config(strict_optional_files=True)

    (xdg / "ralph-workflow-mcp.toml").write_text("", encoding="utf-8")
    local_file = tmp_path / "local.toml"
    local_file.write_bytes(b"[invalid = }")

    with pytest.raises(McpConfigError):
        load_mcp_config(config_path=local_file, strict_optional_files=True)


def test_mcp_fallback_warning_deduplication(tmp_path: Path) -> None:
    bad_file = tmp_path / "dedup_test.toml"
    bad_file.write_bytes(b"[invalid = }")

    records, sink_id = _capture_warnings()
    try:
        load_mcp_config(config_path=bad_file)
        assert len(records) == 1

        load_mcp_config(config_path=bad_file)
        assert len(records) == 1

        bad_file.write_bytes(b"[another_bad = }")
        load_mcp_config(config_path=bad_file)
        assert len(records) == 2

        reset_fallback_warning_registry()
        load_mcp_config(config_path=bad_file)
        assert len(records) == 3
    finally:
        logger.remove(sink_id)
