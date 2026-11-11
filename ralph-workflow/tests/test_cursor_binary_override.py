from __future__ import annotations

from pathlib import Path

import pytest

from ralph.cli.commands.smoke_binary_override import (
    apply_smoke_binary_override,
    apply_smoke_binary_overrides_to_config,
    resolve_smoke_binary_override,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig


class TestCursorBinaryOverride:
    @pytest.mark.timeout_seconds(3)
    def test_ralph_cursor_binary_overrides_cmd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        wrapper_path = tmp_path / "cursor-wrapper.sh"
        wrapper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper_path.chmod(0o755)
        monkeypatch.setenv("RALPH_CURSOR_BINARY", str(wrapper_path))
        cursor_config = AgentConfig(
            cmd="agent",
            transport=AgentTransport.CURSOR,
        )

        result = apply_smoke_binary_override(cursor_config)

        assert result.cmd != "agent"
        assert str(wrapper_path) in result.cmd

    @pytest.mark.timeout_seconds(3)
    def test_resolve_cursor_binary_override_normalizes_relative_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        relative_target = tmp_path / "cursor-wrapper.sh"
        relative_target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        relative_target.chmod(0o755)
        monkeypatch.setenv("RALPH_CURSOR_BINARY", "cursor-wrapper.sh")
        monkeypatch.chdir(tmp_path)

        resolved = resolve_smoke_binary_override(AgentTransport.CURSOR)

        assert resolved is not None
        assert Path(resolved).is_absolute()
        assert Path(resolved).resolve() == relative_target.resolve()

    @pytest.mark.timeout_seconds(3)
    def test_resolve_cursor_binary_override_returns_none_for_missing_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RALPH_CURSOR_BINARY", "/nonexistent/path/to/cursor-wrapper.sh")
        resolved = resolve_smoke_binary_override(AgentTransport.CURSOR)
        assert resolved is None

    @pytest.mark.timeout_seconds(3)
    def test_maybe_apply_cursor_binary_override_ignores_nonexecutable_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cursor_config = AgentConfig(
            cmd="agent",
            transport=AgentTransport.CURSOR,
        )
        monkeypatch.setenv("RALPH_CURSOR_BINARY", "/etc/hosts")
        result = apply_smoke_binary_override(cursor_config)
        assert result.cmd == "agent"

    @pytest.mark.timeout_seconds(3)
    def test_apply_cursor_binary_override_to_config_ignores_nonexecutable_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = UnifiedConfig(
            agents={
                "cursor/auto": AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
                "claude/haiku": AgentConfig(
                    cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE
                ),
            }
        )
        monkeypatch.setenv("RALPH_CURSOR_BINARY", "/etc/hosts")
        result = apply_smoke_binary_overrides_to_config(config)
        assert result.agents["cursor/auto"].cmd == "agent"
        assert result.agents["claude/haiku"].cmd == "claude"
