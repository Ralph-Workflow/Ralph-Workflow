"""Contract tests for the ``RALPH_CURSOR_BINARY`` CLI-boundary override.

These tests live outside ``tests/test_cli_smoke.py`` (which is module-marked
``pytestmark = pytest.mark.smoke`` and excluded by ``addopts = -m "not smoke"``
in ``pytest.ini``) so the AC-12 contract is exercised under the 60-second
combined test budget in ``make verify``.  The tests are pure black-box
(``monkeypatch`` env-var manipulation, no live subprocess, no live network)
and follow the existing AGY-binary-override pattern documented in
``tests/test_agy_plumbing_mock.py``.

Mirrors the AGY override pattern at
``ralph.cli.commands.smoke._maybe_apply_agy_binary_override`` /
``_resolve_agy_binary_override``:

* A relative override is resolved against the current working directory so
  a downstream :class:`subprocess.Popen` always sees an absolute path.
* The path must resolve to a regular file with executable bits set, or to a
  name ``shutil.which`` can locate on ``PATH``.
* When validation fails, a WARNING is logged and ``None`` is returned so
  the caller falls back to the real ``agent`` binary on ``PATH``.

Unlike AGY there is no bundled mock binary for Cursor, so the override is a
wrapper script the operator wires themselves (e.g. a telemetry-injecting
wrapper or a test stub).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.cli.commands import smoke as smoke_module
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig


class TestCursorBinaryOverride:
    """Pin AC-12: ``RALPH_CURSOR_BINARY`` honors the operator's path override.

    Mirrors the AGY override pattern: a relative override is resolved
    against the current working directory so downstream
    :class:`subprocess.Popen` always sees an absolute path.  The path
    must resolve to a regular file with executable bits set, or to a
    name ``shutil.which`` can locate on ``PATH``.  When validation
    fails a WARNING is logged and ``None`` is returned so the caller
    falls back to the real ``agent`` binary on ``PATH``.

    Unlike AGY there is no bundled mock binary for Cursor, so the
    override is a wrapper script the operator wires themselves (e.g.
    a telemetry-injecting wrapper or a test stub).
    """

    @pytest.mark.timeout_seconds(3)
    def test_ralph_cursor_binary_overrides_cmd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An operator-supplied ``RALPH_CURSOR_BINARY`` is applied to the cursor config.

        Mirrors the documented ``RALPH_AGY_BINARY`` contract:
        ``RALPH_CURSOR_BINARY`` is honored at the CLI boundary
        (not by the runtime), the override path is resolved to an
        absolute path so a downstream ``subprocess.Popen`` never
        sees a cwd-relative binary, and the path is shlex-quoted
        when written into ``cmd`` so a wrapper path that contains
        spaces is preserved as a single argv token.
        """
        wrapper_path = tmp_path / "cursor-wrapper.sh"
        wrapper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper_path.chmod(0o755)
        monkeypatch.setenv("RALPH_CURSOR_BINARY", str(wrapper_path))
        cursor_config = AgentConfig(
            cmd="agent",
            transport=AgentTransport.CURSOR,
        )

        result = smoke_module._maybe_apply_cursor_binary_override(cursor_config)

        # The override is applied: cmd is no longer the bare ``agent``.
        assert result.cmd != "agent"
        # The wrapper path is preserved in the cmd (shlex-quoted).
        assert str(wrapper_path) in result.cmd

    @pytest.mark.timeout_seconds(3)
    def test_resolve_cursor_binary_override_normalizes_relative_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """``_resolve_cursor_binary_override`` returns the absolute path for a relative override."""
        relative_target = tmp_path / "cursor-wrapper.sh"
        relative_target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        relative_target.chmod(0o755)
        monkeypatch.setenv("RALPH_CURSOR_BINARY", "cursor-wrapper.sh")
        monkeypatch.chdir(tmp_path)

        resolved = smoke_module._resolve_cursor_binary_override()

        assert resolved is not None
        assert Path(resolved).is_absolute()
        assert Path(resolved).resolve() == relative_target.resolve()

    @pytest.mark.timeout_seconds(3)
    def test_resolve_cursor_binary_override_returns_none_for_missing_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_resolve_cursor_binary_override`` returns ``None`` for a non-executable override."""
        monkeypatch.setenv(
            "RALPH_CURSOR_BINARY", "/nonexistent/path/to/cursor-wrapper.sh"
        )
        resolved = smoke_module._resolve_cursor_binary_override()
        assert resolved is None

    @pytest.mark.timeout_seconds(3)
    def test_maybe_apply_cursor_binary_override_ignores_nonexecutable_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-executable ``RALPH_CURSOR_BINARY`` path is ignored (cmd unchanged)."""
        cursor_config = AgentConfig(
            cmd="agent",
            transport=AgentTransport.CURSOR,
        )
        monkeypatch.setenv("RALPH_CURSOR_BINARY", "/etc/hosts")
        result = smoke_module._maybe_apply_cursor_binary_override(cursor_config)
        # The override is not applied: cmd is the bare ``agent`` binary.
        assert result.cmd == "agent"

    @pytest.mark.timeout_seconds(3)
    def test_apply_cursor_binary_override_to_config_ignores_nonexecutable_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_apply_cursor_binary_override_to_config`` ignores a non-executable override."""
        config = UnifiedConfig(
            agents={
                "cursor/auto": AgentConfig(
                    cmd="agent", transport=AgentTransport.CURSOR
                ),
                "claude/haiku": AgentConfig(
                    cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE
                ),
            }
        )
        monkeypatch.setenv("RALPH_CURSOR_BINARY", "/etc/hosts")
        result = smoke_module._apply_cursor_binary_override_to_config(config)
        # The override is not applied: the cursor cmd is unchanged.
        assert result.agents["cursor/auto"].cmd == "agent"
        # Non-cursor agents are preserved.
        assert result.agents["claude/haiku"].cmd == "claude"
