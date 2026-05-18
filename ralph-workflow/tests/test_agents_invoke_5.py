"""Tests for agent command construction."""

from __future__ import annotations

import json
import threading
import tomllib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

from ralph.agents import invoke as invoke_module
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from collections.abc import Iterable

    import pytest


_EXPECTED_DESCENDANT_LIVENESS_CHECKS = 2


def _json_object(raw: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(raw))


def _toml_object(raw: str) -> dict[str, object]:
    return cast("dict[str, object]", tomllib.loads(raw))


def _env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return cast("dict[str, str]", env_obj)


def _argv(args: tuple[object, ...]) -> list[str]:
    return list(cast("Iterable[str]", args[0]))


class _BlockingStdout:
    """Stdout that blocks forever — drives the idle timeout path.

    Uses FakeClock-aware coordination to avoid real wall-clock waits.
    The stdout iterator yields nothing and raises StopIteration immediately,
    but sets a done event that the test controls. The main loop's
    FakeClock.wait_for_event advances time until the watchdog fires.
    """

    def __init__(self, done_event: threading.Event | None = None) -> None:
        self._done_event = done_event or threading.Event()

    def __iter__(self) -> _BlockingStdout:
        return self

    def __next__(self) -> str:
        raise StopIteration


class _PreloadedStdout:
    """Stdout that yields pre-loaded lines and then closes."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def __iter__(self) -> _PreloadedStdout:
        return self

    def __next__(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        raise StopIteration


class _FakeInvokeProcess:
    """Minimal subprocess.Popen stand-in for integration tests."""

    pid: int = 77777

    def __init__(self, stdout: object = None) -> None:
        self.stdout = stdout or _BlockingStdout()
        self.stderr = SimpleNamespace(read=lambda: "")
        self.returncode: int | None = None

    def __enter__(self) -> _FakeInvokeProcess:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> Literal[False]:
        return False

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def poll(self) -> int | None:
        return self.returncode


class _CallbackFakeClock(FakeClock):
    """FakeClock that triggers threading.Events at scheduled fake-time points."""

    def __init__(self, start: float = 0.0) -> None:
        super().__init__(start)
        self._listeners: list[tuple[float, threading.Event]] = []

    def _trigger_listeners(self) -> None:
        triggered = [ev for target, ev in self._listeners if self._now >= target]
        if triggered:
            for ev in triggered:
                ev.set()
            self._listeners = [(t, ev) for t, ev in self._listeners if self._now < t]

    def sleep(self, seconds: float) -> None:
        self._now += seconds
        self._trigger_listeners()

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        self._now += seconds
        self._trigger_listeners()
        return event.is_set()

    def wait_until(self, target: float) -> threading.Event:
        """Return an event that fires when fake time reaches target."""
        ev = threading.Event()
        if self._now >= target:
            ev.set()
        else:
            self._listeners.append((target, ev))
        return ev


class _EventTriggeredStdout:
    """Stdout that yields one line when an event fires, then EOF."""

    def __init__(self, line: str, trigger: threading.Event) -> None:
        self._line = line
        self._trigger = trigger
        self._done = False

    def __iter__(self) -> _EventTriggeredStdout:
        return self

    def __next__(self) -> str:
        if not self._done:
            self._trigger.wait()
            self._done = True
            return self._line
        raise StopIteration


class _ScheduledStdout:
    """Stdout that yields each line after its corresponding event fires."""

    def __init__(self, scheduled_lines: list[tuple[str, threading.Event]]) -> None:
        self._scheduled_lines = list(scheduled_lines)

    def __iter__(self) -> _ScheduledStdout:
        return self

    def __next__(self) -> str:
        if not self._scheduled_lines:
            raise StopIteration
        line, trigger = self._scheduled_lines.pop(0)
        trigger.wait()
        return line


class _ClockBasedLivenessProbe:
    """Probe that reports children active until a fake-clock threshold is reached."""

    def __init__(self, clock: FakeClock, active_until: float) -> None:
        self._clock = clock
        self._active_until = active_until

    def any_agent_active(self, label_prefix: str) -> bool:
        return self._clock.monotonic() < self._active_until


class TestResolveInvocationRuntime:
    def test_opencode_uses_config_content_from_base_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        config = AgentConfig(
            cmd="opencode",
            output_flag="--json-stream",
            transport=AgentTransport.OPENCODE,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
        captured: list[str | None] = []

        def fake_build(config_content: str | None, endpoint: str) -> tuple[str, list[object]]:
            captured.append(config_content)
            return ("{}", [])

        monkeypatch.setattr(invoke_module, "build_opencode_provider_config", fake_build)
        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        invoke_module.resolve_invocation_runtime(
            config,
            extra_env,
            None,
            _base_env={"OPENCODE_CONFIG_CONTENT": "injected-content"},
        )
        assert captured[0] == "injected-content"

    def test_codex_uses_home_from_base_env(self, monkeypatch: pytest.MonkeyPatch) -> None:

        config = AgentConfig(
            cmd="codex",
            output_flag="",
            transport=AgentTransport.CODEX,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
        captured: list[str | None] = []

        def fake_prepare(
            endpoint: str | None,
            *,
            workspace_path: object,
            existing_home: str | None,
            system_prompt_file: object,
        ) -> tuple[str, list[object]]:
            captured.append(existing_home)
            return ("/fake/home", [])

        monkeypatch.setattr(invoke_module, "prepare_codex_home_with_upstreams", fake_prepare)
        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        invoke_module.resolve_invocation_runtime(
            config, extra_env, None, _base_env={"CODEX_HOME": "/injected/home"}
        )
        assert captured[0] == "/injected/home"
