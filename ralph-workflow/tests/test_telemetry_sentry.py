"""Tests for ralph.telemetry._sentry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import cast, get_args

import pytest

from ralph.config.agent_transport import AgentTransport
from ralph.platform.architecture import Architecture
from ralph.platform.environment_info import EnvironmentInfo
from ralph.platform.models import PlatformInfo
from ralph.platform.operating_system import OperatingSystem
from ralph.policy.models._types import PhaseRole
from ralph.runtime import _version_info
from ralph.runtime.environment import RuntimeEnvironment
from ralph.telemetry import _sentry
from ralph.telemetry._sentry import (
    _scrub_event,
    _scrub_obj,
    init_sentry,
    is_telemetry_disabled,
)


def test_init_sentry_calls_sentry_init(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    def capture_init(**kwargs: object) -> None:
        captured.append(dict(kwargs))

    def noop_set_user(arg: object) -> None:
        pass

    def noop_set_tag(key: object, val: object) -> None:
        pass

    monkeypatch.setattr("sentry_sdk.init", capture_init)
    monkeypatch.setattr("sentry_sdk.set_user", noop_set_user)
    monkeypatch.setattr("sentry_sdk.set_tag", noop_set_tag)

    init_sentry("a" * 32, "b" * 64)

    assert len(captured) == 1
    kwargs = captured[0]
    assert kwargs.get("send_default_pii") is False
    assert "dsn" in kwargs
    assert "sentry.io" in str(kwargs["dsn"])
    assert "before_send" in kwargs
    assert "before_send_transaction" in kwargs
    assert kwargs.get("traces_sample_rate") == 1.0
    assert kwargs.get("default_integrations") is False
    assert kwargs.get("auto_enabling_integrations") is False
    assert kwargs.get("profiles_sample_rate") == 0.0
    assert kwargs.get("profile_session_sample_rate") == 0.0
    assert "profile_lifecycle" not in kwargs
    assert kwargs.get("auto_session_tracking") is True
    assert kwargs.get("send_client_reports") is True
    assert kwargs.get("enable_logs") is False
    assert kwargs.get("environment") in {"default", "ci"}
    release = kwargs.get("release")
    assert isinstance(release, str)
    assert release.startswith("ralph-workflow@")
    # Defense in depth: locals like ``inline_prompt`` must NEVER be forwarded
    # even if the scrubber misses a prefix. Disabling local-variable capture
    # at the SDK level removes the surface entirely.
    assert kwargs.get("include_local_variables") is False


def test_init_sentry_sets_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    user_calls: list[object] = []

    def noop_init(**kwargs: object) -> None:
        pass

    def capture_set_user(arg: object) -> None:
        user_calls.append(arg)

    def noop_set_tag(key: object, val: object) -> None:
        pass

    monkeypatch.setattr("sentry_sdk.init", noop_init)
    monkeypatch.setattr("sentry_sdk.set_user", capture_set_user)
    monkeypatch.setattr("sentry_sdk.set_tag", noop_set_tag)

    uid = "x" * 32
    init_sentry(uid, "y" * 64)

    assert len(user_calls) == 1
    arg = user_calls[0]
    assert isinstance(arg, dict)
    assert arg == {"id": uid}
    assert len(arg) == 1


def test_init_sentry_sets_session_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    tag_calls: list[tuple[str, str]] = []

    def noop_init(**kwargs: object) -> None:
        pass

    def noop_set_user(arg: object) -> None:
        pass

    def capture_set_tag(k: str, v: str) -> None:
        tag_calls.append((k, v))

    monkeypatch.setattr("sentry_sdk.init", noop_init)
    monkeypatch.setattr("sentry_sdk.set_user", noop_set_user)
    monkeypatch.setattr("sentry_sdk.set_tag", capture_set_tag)

    sid = "z" * 64
    init_sentry("a" * 32, sid)

    assert ("session_id", sid) in tag_calls


def test_scrub_obj_replaces_home_prefix_in_string(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    data: dict[str, object] = {"path": f"{fake_home}/projects"}
    _scrub_obj(data)

    assert data["path"] == "~/projects"


def test_scrub_obj_handles_nested_dicts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    data: dict[str, object] = {"outer": {"inner": f"{fake_home}/x"}}
    _scrub_obj(data)

    outer = data["outer"]
    assert isinstance(outer, dict)
    assert outer["inner"] == "~/x"


def test_scrub_obj_handles_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    lst: list[object] = [f"{fake_home}/a", f"{fake_home}/b"]
    _scrub_obj(lst)

    assert lst[0] == "~/a"
    assert lst[1] == "~/b"


def test_scrub_obj_ignores_non_string_values(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    data: dict[str, object] = {"n": 42, "b": True, "x": None}
    _scrub_obj(data)

    assert data["n"] == 42
    assert data["b"] is True
    assert data["x"] is None


def test_scrub_event_removes_server_name() -> None:
    event: dict[str, object] = {"server_name": "myhost.local", "message": "test"}
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    assert "server_name" not in result
    assert result.get("message") == "test"


# ---------------------------------------------------------------------------
# Opt-out flag (RALPH_DISABLE_TELEMETRY) — red-phase tests for step 1.
# ---------------------------------------------------------------------------


def test_is_telemetry_disabled_returns_false_when_unset() -> None:
    env: dict[str, str] = {}
    assert is_telemetry_disabled(env) is False


def test_is_telemetry_disabled_returns_false_for_empty_value() -> None:
    env: dict[str, str] = {"RALPH_DISABLE_TELEMETRY": ""}
    assert is_telemetry_disabled(env) is False


def test_is_telemetry_disabled_true_values() -> None:
    for value in ("1", "true", "yes", "on"):
        env: dict[str, str] = {"RALPH_DISABLE_TELEMETRY": value}
        assert is_telemetry_disabled(env) is True, f"expected True for {value!r}"


def test_is_telemetry_disabled_true_values_case_insensitive() -> None:
    for value in ("TRUE", "Yes", "On", "1"):
        env: dict[str, str] = {"RALPH_DISABLE_TELEMETRY": value}
        assert is_telemetry_disabled(env) is True, f"expected True for {value!r}"


def test_is_telemetry_disabled_false_values() -> None:
    for value in ("0", "false", "no", "off"):
        env: dict[str, str] = {"RALPH_DISABLE_TELEMETRY": value}
        assert is_telemetry_disabled(env) is False, f"expected False for {value!r}"


def test_is_telemetry_disabled_reads_os_environ_when_no_env_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)
    assert _sentry.is_telemetry_disabled() is False

    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
    assert _sentry.is_telemetry_disabled() is True


# ---------------------------------------------------------------------------
# Extra-scrub-prefix redaction (cwd, argv) — red-phase tests for step 1.
# ---------------------------------------------------------------------------


def test_scrub_obj_redacts_extra_scrub_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ("/Volumes/x",))
    monkeypatch.setattr(_sentry, "_HOME_PREFIX", "/nonexistent/home")

    data: dict[str, object] = {"path": "/Volumes/x/project/file.py"}
    _sentry._scrub_obj(data)
    assert data["path"] == "<redacted>/project/file.py"


def test_scrub_obj_redacts_each_extra_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ("/Volumes/x", "/tmp/argv1"))
    monkeypatch.setattr(_sentry, "_HOME_PREFIX", "/nonexistent/home")

    data: dict[str, object] = {
        "a": "/Volumes/x/proj/x.py",
        "b": "/tmp/argv1/some/arg",
        "c": "/var/log/other.txt",
    }
    _sentry._scrub_obj(data)
    assert data["a"] == "<redacted>/proj/x.py"
    assert data["b"] == "<redacted>/some/arg"
    assert data["c"] == "/var/log/other.txt"


def test_scrub_obj_home_prefix_still_maps_to_tilde(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = "/fake/home/user"
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ())
    monkeypatch.setattr(_sentry, "_HOME_PREFIX", fake_home)

    data: dict[str, object] = {"path": f"{fake_home}/projects"}
    _sentry._scrub_obj(data)
    assert data["path"] == "~/projects"


def test_scrub_obj_handles_nested_dicts_with_extra_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ("/Volumes/x",))
    monkeypatch.setattr(_sentry, "_HOME_PREFIX", "/nonexistent/home")

    data: dict[str, object] = {"outer": {"inner": "/Volumes/x/y"}}
    _sentry._scrub_obj(data)
    outer = data["outer"]
    assert isinstance(outer, dict)
    assert outer["inner"] == "<redacted>/y"


def test_scrub_obj_handles_lists_with_extra_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ("/Volumes/x",))
    monkeypatch.setattr(_sentry, "_HOME_PREFIX", "/nonexistent/home")

    lst: list[object] = ["/Volumes/x/a", "/Volumes/x/b"]
    _sentry._scrub_obj(lst)
    assert lst[0] == "<redacted>/a"
    assert lst[1] == "<redacted>/b"


# ---------------------------------------------------------------------------
# Frame scrubbing (exception.values[*].stacktrace.frames[*].abs_path) —
# red-phase tests for step 1.
# ---------------------------------------------------------------------------


def test_scrub_event_drops_abs_path_in_frames() -> None:
    event: dict[str, object] = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "foo.py",
                                "abs_path": "/Users/jane/projects/secret/foo.py",
                            }
                        ]
                    }
                }
            ]
        }
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert "abs_path" not in frame


def test_scrub_event_basenames_absolute_filename() -> None:
    event: dict[str, object] = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "/Users/jane/projects/secret/foo.py",
                                "abs_path": "/Users/jane/projects/secret/foo.py",
                            }
                        ]
                    }
                }
            ]
        }
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert frame["filename"] == "foo.py"


def test_scrub_event_basenames_relative_filename() -> None:
    """Relative path-like filenames MUST collapse to their basename.

    Regression: a relative frame filename such as ``ralph/foo.py`` reveals
    codebase structure (module hierarchy, package layout) and must be
    redacted to its basename. The earlier behavior preserved relative
    filenames verbatim, which leaked codebase identity — contradicting
    AC-06 ("no project file paths leave the process") and the README
    privacy claim that telemetry never identifies the codebase.
    """
    event: dict[str, object] = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"filename": "ralph/foo.py"},
                        ]
                    }
                }
            ]
        }
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert frame["filename"] == "foo.py"


def test_scrub_event_basenames_windows_relative_filename() -> None:
    """Windows-style relative filenames (``src\\foo.py``) collapse to basename."""
    event: dict[str, object] = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"filename": "src\\foo.py"},
                        ]
                    }
                }
            ]
        }
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert frame["filename"] == "foo.py"


def test_scrub_event_preserves_bare_module_name() -> None:
    """Bare module names without a path separator are too generic to leak identity.

    ``foo.py`` (no ``/`` or ``\\``) does not identify a codebase, so the
    scrubber leaves it intact. This bounds the redaction to filenames
    that actually carry path information.
    """
    event: dict[str, object] = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"filename": "foo.py"},
                        ]
                    }
                }
            ]
        }
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert frame["filename"] == "foo.py"


def test_scrub_event_drops_server_name_alongside_frame_scrubbing() -> None:
    event: dict[str, object] = {
        "server_name": "myhost.local",
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"filename": "/x.py", "abs_path": "/x.py"},
                        ]
                    }
                }
            ]
        },
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    assert "server_name" not in result
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert "abs_path" not in frame
    assert frame["filename"] == "x.py"


def test_init_sentry_initializes_extra_scrub_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """init_sentry must populate _EXTRA_SCRUB_PREFIXES from cwd + argv paths."""
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ())

    init_calls: list[dict[str, object]] = []

    def capture_init(**kwargs: object) -> None:
        init_calls.append(dict(kwargs))

    monkeypatch.setattr("sentry_sdk.init", capture_init)
    monkeypatch.setattr("sentry_sdk.set_user", lambda arg: None)
    monkeypatch.setattr("sentry_sdk.set_tag", lambda k, v: None)

    fake_cwd = "/tmp/fake-cwd-for-telemetry-test"
    fake_argv0 = "/tmp/fake-argv0-for-telemetry-test"
    monkeypatch.setattr("os.getcwd", lambda: fake_cwd)
    monkeypatch.setattr("sys.argv", [fake_argv0, "--prompt", "secret prompt"])

    init_sentry("a" * 32, "b" * 64)

    prefixes = _sentry._EXTRA_SCRUB_PREFIXES
    assert fake_cwd in prefixes
    assert fake_argv0 in prefixes
    # The prompt string is NOT a path; it must NOT be added to prefixes.
    assert "secret prompt" not in prefixes
    # argv0 was added, so init ran.
    assert len(init_calls) == 1


# ---------------------------------------------------------------------------
# Cross-platform absolute-path recognition (POSIX + Windows) — regression
# tests for the cross-platform scrubber (AC-04 / AC-06).
# ---------------------------------------------------------------------------


def test_is_absolute_filename_posix() -> None:
    assert _sentry._is_absolute_filename("/Users/jane/foo.py") is True
    assert _sentry._is_absolute_filename("/") is True


def test_is_absolute_filename_windows_drive_letter_backslash() -> None:
    assert _sentry._is_absolute_filename("C:\\Users\\jane\\foo.py") is True


def test_is_absolute_filename_windows_drive_letter_forward_slash() -> None:
    assert _sentry._is_absolute_filename("C:/Users/jane/foo.py") is True


def test_is_absolute_filename_windows_unc() -> None:
    assert _sentry._is_absolute_filename("\\\\server\\share\\foo.py") is True


def test_is_absolute_filename_rejects_relative_paths() -> None:
    assert _sentry._is_absolute_filename("ralph/foo.py") is False
    assert _sentry._is_absolute_filename("foo.py") is False
    assert _sentry._is_absolute_filename("foo:bar") is False
    assert _sentry._is_absolute_filename("") is False
    assert _sentry._is_absolute_filename("C:foo.py") is False


def test_is_absolute_filename_rejects_non_string() -> None:
    assert _sentry._is_absolute_filename(None) is False
    assert _sentry._is_absolute_filename(42) is False


def test_scrub_event_basenames_windows_absolute_filename() -> None:
    event: dict[str, object] = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "C:\\Users\\jane\\secret\\foo.py",
                                "abs_path": "C:\\Users\\jane\\secret\\foo.py",
                            }
                        ]
                    }
                }
            ]
        }
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert "abs_path" not in frame
    assert frame["filename"] == "foo.py"


def test_scrub_event_basenames_windows_unc_filename() -> None:
    event: dict[str, object] = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "\\\\nas\\share\\project\\bar.py",
                                "abs_path": "\\\\nas\\share\\project\\bar.py",
                            }
                        ]
                    }
                }
            ]
        }
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    exc = result["exception"]
    assert isinstance(exc, dict)
    values = exc["values"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    frames = first["stacktrace"]["frames"]
    assert isinstance(frames, list)
    frame = frames[0]
    assert isinstance(frame, dict)
    assert "abs_path" not in frame
    assert frame["filename"] == "bar.py"


def test_init_sentry_initializes_extra_scrub_prefixes_with_windows_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows-style absolute argv entries must end up in ``_EXTRA_SCRUB_PREFIXES``."""
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ())

    monkeypatch.setattr("sentry_sdk.init", lambda **kwargs: None)
    monkeypatch.setattr("sentry_sdk.set_user", lambda arg: None)
    monkeypatch.setattr("sentry_sdk.set_tag", lambda k, v: None)

    fake_cwd = "C:\\Projects\\ralph-workflow"
    win_argv0 = "C:\\Users\\jane\\repo\\run.py"
    monkeypatch.setattr("os.getcwd", lambda: fake_cwd)
    monkeypatch.setattr(
        "sys.argv",
        [win_argv0, "--prompt", "secret prompt"],
    )

    init_sentry("a" * 32, "b" * 64)

    prefixes = _sentry._EXTRA_SCRUB_PREFIXES
    assert fake_cwd in prefixes
    assert win_argv0 in prefixes
    assert "secret prompt" not in prefixes


def test_build_extra_scrub_prefixes_filters_non_absolute_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argv entries that are not absolute paths MUST NOT be scrub prefixes.

    Regression: flag values, prompts, and inline arguments would otherwise leak
    into the scrub-prefix list and cause unrelated strings to be redacted.
    """
    monkeypatch.setattr(_sentry, "_EXTRA_SCRUB_PREFIXES", ())
    monkeypatch.setattr("os.getcwd", lambda: "/tmp/fake-cwd")
    monkeypatch.setattr(
        "sys.argv",
        [
            "/tmp/fake-argv0",
            "--prompt",
            "a totally benign prompt",
            "C:\\Users\\jane\\repo\\run.py",
            "ralph/cli/main.py",  # not absolute
            "",  # empty
            "42",  # not a path
        ],
    )
    prefixes = _sentry._build_extra_scrub_prefixes()
    assert "/tmp/fake-cwd" in prefixes
    assert "/tmp/fake-argv0" in prefixes
    assert "C:\\Users\\jane\\repo\\run.py" in prefixes
    assert "--prompt" not in prefixes
    assert "a totally benign prompt" not in prefixes
    assert "ralph/cli/main.py" not in prefixes
    assert "" not in prefixes
    assert "42" not in prefixes


# ---------------------------------------------------------------------------
# set_environment_context — red-phase tests for step 3.
# ---------------------------------------------------------------------------


def _make_runtime_env(*, in_virtualenv: bool, fake_sys: object) -> RuntimeEnvironment:
    """Build a fake RuntimeEnvironment for the in_virtualenv bool test seam."""
    py = _version_info.PythonVersionInfo(
        major=3,
        minor=12,
        micro=5,
        releaselevel="final",
        serial=0,
        implementation="CPython",
        executable=Path(getattr(fake_sys, "executable", "/usr/bin/python3.12")),
        version="3.12.5",
    )
    return RuntimeEnvironment(
        python=py,
        executable=Path(getattr(fake_sys, "executable", "/usr/bin/python3.12")),
        prefix=Path(getattr(fake_sys, "prefix", "/opt/fake-venv")),
        base_prefix=Path(getattr(fake_sys, "base_prefix", "/usr")),
        exec_prefix=Path(getattr(fake_sys, "exec_prefix", "/opt/fake-venv")),
        base_exec_prefix=Path(getattr(fake_sys, "base_exec_prefix", "/usr")),
        in_virtualenv=in_virtualenv,
        virtualenv_path=Path("/opt/fake-venv") if in_virtualenv else None,
        env=MappingProxyType({}),
    )


def test_set_environment_context_emits_tags_and_runtime_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_calls: list[tuple[str, str]] = []
    context_calls: list[tuple[str, dict[str, object]]] = []

    def capture_set_tag(key: str, value: str) -> None:
        tag_calls.append((key, value))

    def capture_set_context(name: str, data: dict[str, object]) -> None:
        context_calls.append((name, dict(data)))

    monkeypatch.setattr("sentry_sdk.set_tag", capture_set_tag)
    monkeypatch.setattr("sentry_sdk.set_context", capture_set_context)

    fixed_platform = PlatformInfo(
        os=OperatingSystem.LINUX,
        architecture=Architecture.ARM64,
        environment=EnvironmentInfo(
            ci=True,
            container=True,
            wsl=False,
            codespaces=False,
            ssh=False,
        ),
        package_manager="apt",
    )
    monkeypatch.setattr(_sentry, "current_platform", lambda: fixed_platform)
    monkeypatch.setattr(_sentry, "ralph_version", "9.9.9-test")

    fixed_version = _version_info.PythonVersionInfo(
        major=3,
        minor=12,
        micro=5,
        releaselevel="final",
        serial=0,
        implementation="CPython",
        executable=Path("/usr/bin/python3.12"),
        version="3.12.5",
    )

    monkeypatch.setattr(
        _sentry.PythonVersionInfo,
        "from_sys",
        classmethod(lambda cls, sys_module: fixed_version),
    )

    class _FakeSysModule:
        prefix = "/opt/fake-venv"
        base_prefix = "/usr"
        exec_prefix = "/opt/fake-venv"
        base_exec_prefix = "/usr"
        executable = "/usr/bin/python3.12"

    monkeypatch.setattr(
        _sentry,
        "detect_runtime_environment",
        lambda env=None, sys_module=None: _make_runtime_env(
            in_virtualenv=True,
            fake_sys=_FakeSysModule(),
        ),
    )

    _sentry.set_environment_context()

    tag_map = dict(tag_calls)
    assert tag_map.get("os") == "linux"
    assert tag_map.get("architecture") == "arm64"
    assert tag_map.get("python_version") == "3.12.5"
    assert tag_map.get("ralph_version") == "9.9.9-test"

    runtime_context = next(
        (c for c in context_calls if c[0] == "runtime"),
        None,
    )
    assert runtime_context is not None
    rc = runtime_context[1]
    assert rc.get("python_implementation") == "CPython"
    assert rc.get("in_virtualenv") is True
    assert rc.get("environment_markers") == ["ci", "container"]
    assert rc.get("package_manager") == "apt"

    forbidden_keys = {
        "executable",
        "prefix",
        "base_prefix",
        "exec_prefix",
        "virtualenv_path",
        "env",
    }
    leaked = forbidden_keys & rc.keys()
    assert not leaked, f"runtime context leaked forbidden keys: {leaked}"


def test_set_environment_context_is_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated current_platform failure")

    monkeypatch.setattr("sentry_sdk.set_tag", boom)
    monkeypatch.setattr("sentry_sdk.set_context", boom)
    monkeypatch.setattr(_sentry, "current_platform", boom)
    # Should NOT raise.
    _sentry.set_environment_context()


def test_set_environment_context_empty_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr("sentry_sdk.set_tag", lambda k, v: None)
    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: context_calls.append((name, dict(data))),
    )

    monkeypatch.setattr(
        _sentry,
        "current_platform",
        lambda: PlatformInfo(
            os=OperatingSystem.MACOS,
            architecture=Architecture.X86_64,
            environment=EnvironmentInfo(),
            package_manager=None,
        ),
    )
    monkeypatch.setattr(_sentry, "ralph_version", "0.0.0-test")

    monkeypatch.setattr(
        _sentry.PythonVersionInfo,
        "from_sys",
        classmethod(
            lambda cls, sys_module: _version_info.PythonVersionInfo(
                major=3,
                minor=11,
                micro=0,
                releaselevel="final",
                serial=0,
                implementation="CPython",
                executable=Path("/usr/bin/python3"),
                version="3.11.0",
            )
        ),
    )

    class _FakeSysModule:
        prefix = "/opt/fake-venv"
        base_prefix = "/usr"
        exec_prefix = "/opt/fake-venv"
        base_exec_prefix = "/usr"
        executable = "/usr/bin/python3"

    monkeypatch.setattr(
        _sentry,
        "detect_runtime_environment",
        lambda env=None, sys_module=None: _make_runtime_env(
            in_virtualenv=False,
            fake_sys=_FakeSysModule(),
        ),
    )

    _sentry.set_environment_context()

    runtime_context = next(c for c in context_calls if c[0] == "runtime")
    assert runtime_context[1]["environment_markers"] == []
    assert runtime_context[1]["package_manager"] is None
    assert runtime_context[1]["python_implementation"] == "CPython"
    assert runtime_context[1]["in_virtualenv"] is False


# ---------------------------------------------------------------------------
# Session lifecycle (start, outcome, finalize, flush) — red-phase tests for step 5.
# ---------------------------------------------------------------------------


def test_record_session_start_and_finalize_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", None)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    monkeypatch.setattr(_sentry, "_SESSION_FINALIZED", False)

    context_calls: list[tuple[str, dict[str, object]]] = []
    message_calls: list[tuple[str, str]] = []
    flush_calls: list[float] = []
    start_session_calls: list[str] = []
    end_session_calls: list[bool] = []
    transaction_calls: list[dict[str, object]] = []
    transaction_finishes: list[bool] = []
    breadcrumb_calls: list[dict[str, object]] = []
    metric_count_calls: list[tuple[str, float, dict[str, object] | None]] = []
    metric_distribution_calls: list[tuple[str, float, str | None, dict[str, object] | None]] = []

    class _Transaction:
        def finish(self) -> None:
            transaction_finishes.append(True)

    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: context_calls.append((name, dict(data))),
    )
    monkeypatch.setattr(
        "sentry_sdk.capture_message",
        lambda msg, level=None: message_calls.append((msg, str(level))),
    )

    def capture_flush(timeout: object = None) -> None:
        flush_calls.append(float(timeout) if timeout is not None else -1.0)

    monkeypatch.setattr("sentry_sdk.flush", capture_flush)
    monkeypatch.setattr(
        "sentry_sdk.start_session",
        lambda session_mode="application": start_session_calls.append(str(session_mode)),
    )
    monkeypatch.setattr("sentry_sdk.end_session", lambda: end_session_calls.append(True))
    monkeypatch.setattr(
        "sentry_sdk.start_transaction",
        lambda **kwargs: transaction_calls.append(dict(kwargs)) or _Transaction(),
    )
    monkeypatch.setattr(
        "sentry_sdk.add_breadcrumb",
        lambda **kwargs: breadcrumb_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "sentry_sdk.metrics.count",
        lambda name, value, unit=None, attributes=None: metric_count_calls.append(
            (name, float(value), dict(attributes) if attributes is not None else None)
        ),
    )
    monkeypatch.setattr(
        "sentry_sdk.metrics.distribution",
        lambda name, value, unit=None, attributes=None: metric_distribution_calls.append(
            (
                name,
                float(value),
                str(unit) if unit is not None else None,
                dict(attributes) if attributes is not None else None,
            )
        ),
    )

    _sentry.record_session_start(now=100.0)
    _sentry.set_session_outcome("success")
    duration = _sentry.finalize_session(now=160.0, flush_timeout=2.0)

    assert start_session_calls == ["application"]
    assert end_session_calls == [True]
    assert transaction_calls == [{"op": "cli.run", "name": "ralph.session"}]
    assert transaction_finishes == [True]
    assert any(call.get("category") == "ralph.session" for call in breadcrumb_calls)
    assert ("ralph.session", 1.0, {"outcome": "success"}) in metric_count_calls
    assert (
        "ralph.session.duration",
        60.0,
        "second",
        {"outcome": "success"},
    ) in metric_distribution_calls
    assert duration == pytest.approx(60.0)
    session_context = next(c for c in context_calls if c[0] == "session")
    assert session_context[1]["duration_s"] == pytest.approx(60.0)
    # Explicit timing markers — process-local monotonic values, no wall-clock leak.
    assert session_context[1]["started_monotonic_s"] == pytest.approx(100.0)
    assert session_context[1]["ended_monotonic_s"] == pytest.approx(160.0)
    assert session_context[1]["outcome"] == "success"
    assert [message[0] for message in message_calls] == ["session start", "session end"]
    assert all(message[1] == "info" for message in message_calls)
    assert flush_calls == [2.0]


def test_session_lifecycle_records_utc_start_and_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session context exposes queryable UTC bounds alongside monotonic duration."""
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", None)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    monkeypatch.setattr(_sentry, "_SESSION_FINALIZED", False)

    context_calls: list[tuple[str, dict[str, object]]] = []
    message_calls: list[str] = []
    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: context_calls.append((name, dict(data))),
    )
    monkeypatch.setattr(
        "sentry_sdk.capture_message",
        lambda message, level=None: message_calls.append(str(message)),
    )
    monkeypatch.setattr("sentry_sdk.start_session", lambda session_mode="application": None)
    monkeypatch.setattr("sentry_sdk.start_transaction", lambda **kwargs: None)
    monkeypatch.setattr("sentry_sdk.end_session", lambda: None)
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)

    start_dt = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
    end_dt = datetime(2026, 7, 14, 12, 0, 7, tzinfo=UTC)
    _sentry.record_session_start(now=10.0, now_dt=start_dt)
    assert _sentry.finalize_session(now=17.5, end_dt=end_dt) == pytest.approx(7.5)

    session = next(data for name, data in context_calls if name == "session")
    assert session["started_at_utc"] == "2026-07-14T12:00:00Z"
    assert session["ended_at_utc"] == "2026-07-14T12:00:07Z"
    assert message_calls == ["session start", "session end"]


def test_agent_invocation_uses_safe_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom agent and drain names never enter invocation telemetry."""
    metric_calls: list[tuple[str, dict[str, object] | None]] = []
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(
        "sentry_sdk.metrics.count",
        lambda name, value, attributes=None: metric_calls.append(
            (str(name), dict(attributes) if attributes is not None else None)
        ),
    )
    monkeypatch.setattr(
        "sentry_sdk.metrics.distribution",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("sentry_sdk.add_breadcrumb", lambda **kwargs: None)

    _sentry.record_agent_invocation(
        transport=AgentTransport.GENERIC,
        phase_role="execution",
        drain="customer-secret-drain",
        drain_class="execution",
        pipeline_profile="custom",
        duration_s=2.25,
        outcome="failure",
    )

    name, attributes = metric_calls[0]
    assert name == "ralph.agent.invocation"
    assert attributes == {
        "agent_family": "custom",
        "transport": "generic",
        "pipeline_profile": "custom",
        "phase_role": "execution",
        "drain": "custom",
        "drain_class": "execution",
        "outcome": "failure",
    }
    assert "customer-secret" not in repr(metric_calls)


def test_finalize_session_returns_none_when_not_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_INITIALIZED", False)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", None)

    assert _sentry.finalize_session(now=100.0) is None


def test_finalize_session_emits_explicit_start_end_timing_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the session context MUST include start AND end timing markers.

    Privacy-safe monotonic floats (process-local, no wall-clock leak). The
    README claims "Session timing (start, duration)" is collected; this test
    pins that contract so the analyzer's "no session-start field sent" defect
    cannot silently regress.
    """
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", None)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")

    context_calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: context_calls.append((name, dict(data))),
    )
    monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)

    _sentry.record_session_start(now=200.0)
    _sentry.finalize_session(now=275.5, flush_timeout=2.0)

    session_context = next(c for c in context_calls if c[0] == "session")
    assert session_context[1]["started_monotonic_s"] == pytest.approx(200.0)
    assert session_context[1]["ended_monotonic_s"] == pytest.approx(275.5)
    assert session_context[1]["duration_s"] == pytest.approx(75.5)


def test_finalize_session_timing_markers_are_process_local_not_wallclock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check: timing markers are monotonic floats, not Unix timestamps.

    A Unix timestamp is a 10-digit absolute value; a process-local monotonic
    float is the actual duration of the test execution. We assert the raw
    value remains inside the injected range so a future regression that
    switches to ``time.time()`` would immediately fail this guard.
    """
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", None)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: captured.setdefault(name, dict(data)),
    )
    monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)

    _sentry.record_session_start(now=500.0)
    _sentry.finalize_session(now=600.0, flush_timeout=2.0)

    session = captured.get("session")
    assert isinstance(session, dict)
    started = session["started_monotonic_s"]
    ended = session["ended_monotonic_s"]
    assert isinstance(started, float)
    assert isinstance(ended, float)
    # Unix timestamps are billions; process-local monotonic stays tiny.
    assert started < 1_000_000.0
    assert ended < 1_000_000.0
    assert ended > started


def test_finalize_session_returns_none_when_no_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", None)

    assert _sentry.finalize_session(now=100.0) is None


def test_finalize_session_is_fail_soft_on_flush_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", 100.0)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "failure")
    monkeypatch.setattr(_sentry, "_SESSION_FINALIZED", False)

    monkeypatch.setattr("sentry_sdk.set_context", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **kw: None)

    def flush_raises(timeout: object = None) -> None:
        raise RuntimeError("flush boom")

    monkeypatch.setattr("sentry_sdk.flush", flush_raises)
    # Must NOT raise.
    assert _sentry.finalize_session(now=160.0, flush_timeout=1.0) == pytest.approx(60.0)


def test_flush_telemetry_calls_sentry_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[float] = []

    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: captured.append(float(timeout)))
    _sentry.flush_telemetry(timeout=1.5)
    assert captured == [1.5]


def test_flush_telemetry_is_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(timeout: object = None) -> None:
        raise RuntimeError("flush boom")

    monkeypatch.setattr("sentry_sdk.flush", boom)
    _sentry.flush_telemetry(timeout=0.5)


def test_init_sentry_marks_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_INITIALIZED", False)
    monkeypatch.setattr("sentry_sdk.init", lambda **kwargs: None)
    monkeypatch.setattr("sentry_sdk.set_user", lambda arg: None)
    monkeypatch.setattr("sentry_sdk.set_tag", lambda k, v: None)

    init_sentry("a" * 32, "b" * 64)
    assert _sentry._INITIALIZED is True


def test_set_session_outcome_records_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    _sentry.set_session_outcome("success")
    assert _sentry._SESSION_OUTCOME == "success"
    _sentry.set_session_outcome("interrupted")
    assert _sentry._SESSION_OUTCOME == "interrupted"


# ---------------------------------------------------------------------------
# Local-variable scrubber guard (defense in depth vs. inline_prompt leak).
# ---------------------------------------------------------------------------


def test_scrub_event_redacts_prompt_like_string_anywhere_in_event() -> None:
    """Regression: prompt-shaped strings must not survive the scrubber.

    ``_scrub_event`` must scrub the entire event payload via ``_scrub_obj``
    so any string field (request bodies, exception values, breadcrumbs,
    extra/context blobs) carrying prompt-like text is redacted regardless
    of which key it lives under. The scrubber cannot enumerate every key,
    so this test asserts the recursive walk + prefix-match contract: any
    nested string that contains a ``_HOME_PREFIX`` substring collapses to
    ``~``.
    """
    fake_home = "/Users/jane"
    _sentry._HOME_PREFIX = fake_home
    _sentry._EXTRA_SCRUB_PREFIXES = ()

    event: dict[str, object] = {
        "extra": {
            "request_body": f"{fake_home}/secret/prompt.txt",
            "context": {
                "prompt": f"{fake_home}/projects/secret/prompt.py",
            },
        },
        "breadcrumbs": {
            "values": [
                {"message": f"{fake_home}/data/input.txt"},
            ],
        },
    }
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    extra = result["extra"]
    assert isinstance(extra, dict)
    assert extra["request_body"] == "~/secret/prompt.txt"
    ctx = extra["context"]
    assert isinstance(ctx, dict)
    assert ctx["prompt"] == "~/projects/secret/prompt.py"
    breadcrumbs = result["breadcrumbs"]
    assert isinstance(breadcrumbs, dict)
    values = breadcrumbs["values"]
    assert isinstance(values, list)
    assert values[0]["message"] == "~/data/input.txt"


def test_init_sentry_disables_local_variables_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``include_local_variables=False`` is set on ``sentry_sdk.init``.

    If the SDK ever silently drops this kwarg (e.g. via a stub that ignores
    unknown kwargs), frame locals could leak ``inline_prompt`` and other
    request parameters verbatim. The test pins the kwarg at the call site.
    """
    captured: dict[str, object] = {}

    def capture_init(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("sentry_sdk.init", capture_init)
    monkeypatch.setattr("sentry_sdk.set_user", lambda arg: None)
    monkeypatch.setattr("sentry_sdk.set_tag", lambda k, v: None)

    init_sentry("a" * 32, "b" * 64)

    assert captured.get("include_local_variables") is False


def test_init_sentry_disables_profiling_for_metadata_only_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentry profiling remains disabled because stack samples are not metadata-only."""
    captured: dict[str, object] = {}

    def capture_init(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("sentry_sdk.init", capture_init)
    monkeypatch.setattr("sentry_sdk.set_user", lambda arg: None)
    monkeypatch.setattr("sentry_sdk.set_tag", lambda k, v: None)

    init_sentry("a" * 32, "b" * 64)

    assert captured.get("profiles_sample_rate") == 0.0
    assert captured.get("profile_session_sample_rate") == 0.0
    assert "profile_lifecycle" not in captured


# ---------------------------------------------------------------------------
# record_command_invocation — closed-vocabulary CLI command tag.
# ---------------------------------------------------------------------------


def test_record_command_invocation_sets_command_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered subcommand name forwards as a single set_tag('command', ...)."""
    tag_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "sentry_sdk.set_tag",
        lambda k, v: tag_calls.append((k, v)),
    )
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    _sentry.record_command_invocation("cleanup")

    assert tag_calls == [("command", "cleanup")]


def test_record_command_invocation_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RALPH_DISABLE_TELEMETRY=1, record_command_invocation must not call set_tag."""
    tag_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "sentry_sdk.set_tag",
        lambda k, v: tag_calls.append((k, v)),
    )
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    _sentry.record_command_invocation("cleanup")

    assert tag_calls == []


def test_record_command_invocation_noop_when_uninit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Sentry was never initialized, record_command_invocation is a no-op."""
    tag_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "sentry_sdk.set_tag",
        lambda k, v: tag_calls.append((k, v)),
    )
    monkeypatch.setattr(_sentry, "_INITIALIZED", False)
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    _sentry.record_command_invocation("cleanup")

    assert tag_calls == []


def test_record_command_invocation_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception from sentry_sdk.set_tag MUST NOT propagate to the caller."""

    def boom(k: object, v: object) -> None:
        raise RuntimeError("simulated set_tag boom")

    monkeypatch.setattr("sentry_sdk.set_tag", boom)
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    # Must not raise.
    _sentry.record_command_invocation("cleanup")


# ---------------------------------------------------------------------------
# set_session_wallclock_start — coarse UTC time-of-day buckets.
# ---------------------------------------------------------------------------


def test_set_session_wallclock_start_records_utc_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session payload contains hour_of_day / day_of_week / is_weekday only."""

    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", 100.0)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    monkeypatch.setattr(_sentry, "_SESSION_WALLCLOCK_BUCKETS", None)
    monkeypatch.setattr(_sentry, "_SESSION_FINALIZED", False)

    context_calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: context_calls.append((name, dict(data))),
    )
    monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)

    _sentry.set_session_wallclock_start(now_dt=datetime(2026, 3, 12, 9, 30, tzinfo=UTC))
    _sentry.finalize_session(now=160.0, flush_timeout=2.0)

    session_context = next(c for c in context_calls if c[0] == "session")
    wallclock = session_context[1]["wallclock"]
    assert isinstance(wallclock, dict)
    assert wallclock["hour_of_day"] == 9
    assert wallclock["day_of_week"] == 3  # Thursday
    assert wallclock["is_weekday"] is True


def test_wallclock_no_full_timestamp_no_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wallclock payload has NO iso_timestamp / timestamp / timezone key."""

    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", 100.0)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    monkeypatch.setattr(_sentry, "_SESSION_WALLCLOCK_BUCKETS", None)
    monkeypatch.setattr(_sentry, "_SESSION_FINALIZED", False)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: captured.setdefault(name, dict(data)),
    )
    monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)

    _sentry.set_session_wallclock_start(now_dt=datetime(2026, 3, 12, 9, 30, tzinfo=UTC))
    _sentry.finalize_session(now=160.0, flush_timeout=2.0)

    session = captured.get("session")
    assert isinstance(session, dict)
    wallclock = session["wallclock"]
    assert isinstance(wallclock, dict)
    forbidden = {"iso_timestamp", "timestamp", "timezone", "tz"}
    leaked = forbidden & wallclock.keys()
    assert not leaked, f"wallclock payload leaked forbidden keys: {leaked}"


def test_set_session_wallclock_start_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When telemetry is disabled, the wallclock buckets must not be recorded."""

    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_WALLCLOCK_BUCKETS", None)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    _sentry.set_session_wallclock_start(now_dt=datetime(2026, 3, 12, 9, 30, tzinfo=UTC))

    assert _sentry._SESSION_WALLCLOCK_BUCKETS is None


def test_set_session_wallclock_start_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions during wallclock capture MUST NOT propagate to the caller."""

    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_WALLCLOCK_BUCKETS", None)
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    def boom(_dt: object = None) -> None:
        raise RuntimeError("simulated wallclock boom")

    monkeypatch.setattr(_sentry, "_compute_wallclock_buckets", boom)

    # Must not raise.
    _sentry.set_session_wallclock_start(now_dt=datetime(2026, 3, 12, 9, 30, tzinfo=UTC))


# ---------------------------------------------------------------------------
# record_phase_execution — PhaseRole-keyed aggregate.
# ---------------------------------------------------------------------------


def test_record_phase_execution_accumulates_by_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three records collapse into one session payload keyed by PhaseRole."""
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", 100.0)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    monkeypatch.setattr(_sentry, "_SESSION_WALLCLOCK_BUCKETS", None)
    monkeypatch.setattr(_sentry, "_SESSION_FINALIZED", False)
    monkeypatch.setattr(_sentry, "_PHASE_STATS", {})

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: captured.setdefault(name, dict(data)),
    )
    monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)
    metric_count_calls: list[tuple[str, float, dict[str, object] | None]] = []
    metric_distribution_calls: list[tuple[str, float, str | None, dict[str, object] | None]] = []
    breadcrumb_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "sentry_sdk.metrics.count",
        lambda name, value, unit=None, attributes=None: metric_count_calls.append(
            (name, float(value), dict(attributes) if attributes is not None else None)
        ),
    )
    monkeypatch.setattr(
        "sentry_sdk.metrics.distribution",
        lambda name, value, unit=None, attributes=None: metric_distribution_calls.append(
            (
                name,
                float(value),
                str(unit) if unit is not None else None,
                dict(attributes) if attributes is not None else None,
            )
        ),
    )
    monkeypatch.setattr(
        "sentry_sdk.add_breadcrumb",
        lambda **kwargs: breadcrumb_calls.append(dict(kwargs)),
    )

    _sentry.record_phase_execution(role="execution", duration_s=1, outcome="success")
    _sentry.record_phase_execution(role="execution", duration_s=1, outcome="success")
    _sentry.record_phase_execution(role="execution", duration_s=2, outcome="failure")
    _sentry.finalize_session(now=160.0, flush_timeout=2.0)

    session = captured.get("session")
    assert isinstance(session, dict)
    phases = session["phases"]
    assert isinstance(phases, dict)
    execution = phases["execution"]
    assert isinstance(execution, dict)
    assert execution["count"] == 3
    assert execution["total_duration_s"] == 4
    outcomes = execution["outcomes"]
    assert outcomes == {"success": 2, "failure": 1, "skipped": 0, "crashed": 0}
    assert (
        "ralph.phase",
        1.0,
        {"role": "execution", "outcome": "success"},
    ) in metric_count_calls
    assert (
        "ralph.phase.duration",
        2.0,
        "second",
        {"role": "execution", "outcome": "failure"},
    ) in metric_distribution_calls
    assert any(call.get("category") == "ralph.phase" for call in breadcrumb_calls)


def test_record_phase_execution_drops_unknown_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role outside the PhaseRole closed vocabulary MUST be silently dropped."""
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_PHASE_STATS", {})

    _sentry.record_phase_execution(role="secret-startup-phase", duration_s=1, outcome="success")

    assert _sentry._PHASE_STATS == {}


def test_record_phase_execution_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When telemetry is disabled, record_phase_execution is a no-op."""
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_PHASE_STATS", {})
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    _sentry.record_phase_execution(role="execution", duration_s=1, outcome="success")

    assert _sentry._PHASE_STATS == {}


def test_record_phase_execution_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception from set_tag / collaborators MUST NOT propagate."""
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_PHASE_STATS", {})
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    # Force a KeyError by pre-populating with a non-dict value.
    _sentry._PHASE_STATS["execution"] = cast("dict[str, dict[str, object]]", "this is not a dict")

    # Must not raise even though the stats lookup will fail.
    _sentry.record_phase_execution(role="execution", duration_s=1, outcome="success")


def test_phase_stats_drained_after_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After finalize_session, the _PHASE_STATS accumulator MUST be cleared."""
    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", 100.0)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    monkeypatch.setattr(_sentry, "_SESSION_WALLCLOCK_BUCKETS", None)
    monkeypatch.setattr(_sentry, "_SESSION_FINALIZED", False)
    monkeypatch.setattr(_sentry, "_PHASE_STATS", {})

    monkeypatch.setattr("sentry_sdk.set_context", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **kw: None)
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)

    _sentry.record_phase_execution(role="execution", duration_s=1, outcome="success")
    assert _sentry._PHASE_STATS != {}

    _sentry.finalize_session(now=160.0, flush_timeout=2.0)
    assert _sentry._PHASE_STATS == {}


# ---------------------------------------------------------------------------
# ci / container boolean tags (EnvironmentInfo reuse, no re-detection).
# ---------------------------------------------------------------------------


def test_set_environment_context_ci_container_false_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ci/container tags fire with boolean False when EnvironmentInfo is empty."""
    tag_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        "sentry_sdk.set_tag",
        lambda k, v: tag_calls.append((k, v)),
    )
    monkeypatch.setattr("sentry_sdk.set_context", lambda *a, **kw: None)

    monkeypatch.setattr(
        _sentry,
        "current_platform",
        lambda: PlatformInfo(
            os=OperatingSystem.MACOS,
            architecture=Architecture.X86_64,
            environment=EnvironmentInfo(ci=False, container=False),
            package_manager=None,
        ),
    )
    monkeypatch.setattr(_sentry, "ralph_version", "0.0.0-test")

    monkeypatch.setattr(
        _sentry.PythonVersionInfo,
        "from_sys",
        classmethod(
            lambda cls, sys_module: _version_info.PythonVersionInfo(
                major=3,
                minor=12,
                micro=5,
                releaselevel="final",
                serial=0,
                implementation="CPython",
                executable=Path("/usr/bin/python3.12"),
                version="3.12.5",
            )
        ),
    )

    class _FakeSysModule:
        prefix = "/opt/fake-venv"
        base_prefix = "/usr"
        exec_prefix = "/opt/fake-venv"
        base_exec_prefix = "/usr"
        executable = "/usr/bin/python3.12"

    monkeypatch.setattr(
        _sentry,
        "detect_runtime_environment",
        lambda env=None, sys_module=None: _make_runtime_env(
            in_virtualenv=False, fake_sys=_FakeSysModule()
        ),
    )

    _sentry.set_environment_context()

    tag_map = dict(tag_calls)
    assert "ci" in tag_map
    assert "container" in tag_map
    assert tag_map["ci"] is False
    assert tag_map["container"] is False
    assert isinstance(tag_map["ci"], bool)
    assert isinstance(tag_map["container"], bool)


def test_set_environment_context_ci_container_forward_bool_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ci/container tag VALUES MUST be booleans, never strings or env-var values."""
    tag_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        "sentry_sdk.set_tag",
        lambda k, v: tag_calls.append((k, v)),
    )
    monkeypatch.setattr("sentry_sdk.set_context", lambda *a, **kw: None)

    monkeypatch.setattr(
        _sentry,
        "current_platform",
        lambda: PlatformInfo(
            os=OperatingSystem.LINUX,
            architecture=Architecture.ARM64,
            environment=EnvironmentInfo(ci=True, container=True),
            package_manager="apt",
        ),
    )
    monkeypatch.setattr(_sentry, "ralph_version", "0.0.0-test")

    monkeypatch.setattr(
        _sentry.PythonVersionInfo,
        "from_sys",
        classmethod(
            lambda cls, sys_module: _version_info.PythonVersionInfo(
                major=3,
                minor=12,
                micro=5,
                releaselevel="final",
                serial=0,
                implementation="CPython",
                executable=Path("/usr/bin/python3.12"),
                version="3.12.5",
            )
        ),
    )

    class _FakeSysModule:
        prefix = "/opt/fake-venv"
        base_prefix = "/usr"
        exec_prefix = "/opt/fake-venv"
        base_exec_prefix = "/usr"
        executable = "/usr/bin/python3.12"

    monkeypatch.setattr(
        _sentry,
        "detect_runtime_environment",
        lambda env=None, sys_module=None: _make_runtime_env(
            in_virtualenv=False, fake_sys=_FakeSysModule()
        ),
    )

    _sentry.set_environment_context()

    tag_map = dict(tag_calls)
    assert isinstance(tag_map["ci"], bool)
    assert isinstance(tag_map["container"], bool)
    assert tag_map["ci"] is True
    assert tag_map["container"] is True


# ---------------------------------------------------------------------------
# Closed-vocabulary privacy regression — proves no user-supplied string,
# sys.argv content, prompt, or cwd/path leaves the process via the new
# telemetry. Tagged-name scan + role-set membership + bool-only ci/container
# are the three invariants.
# ---------------------------------------------------------------------------


def _assert_no_forbidden_substrings(
    values: list[object],
    *,
    source: str,
    forbidden_substrings: tuple[str, ...],
) -> None:
    for needle in forbidden_substrings:
        for leaf in values:
            if isinstance(leaf, str):
                assert needle not in leaf.lower(), (
                    f"{source} leaked forbidden substring {needle!r}: {leaf!r}"
                )


def _assert_metrics_are_metadata_only(
    metric_count_calls: list[tuple[str, float, dict[str, object] | None]],
    metric_distribution_calls: list[tuple[str, float, str | None, dict[str, object] | None]],
    *,
    forbidden_substrings: tuple[str, ...],
) -> None:
    allowed_metric_names = {
        "ralph.command",
        "ralph.phase",
        "ralph.phase.duration",
        "ralph.session",
        "ralph.session.duration",
    }
    for metric_name, _value, attrs in metric_count_calls:
        assert metric_name in allowed_metric_names
        flat = [metric_name]
        if attrs is not None:
            flat.extend(_flatten(attrs))
        _assert_no_forbidden_substrings(
            flat,
            source="metric count",
            forbidden_substrings=forbidden_substrings,
        )
    for metric_name, _value, unit, attrs in metric_distribution_calls:
        assert metric_name in allowed_metric_names
        flat = [metric_name, unit]
        if attrs is not None:
            flat.extend(_flatten(attrs))
        _assert_no_forbidden_substrings(
            flat,
            source="metric distribution",
            forbidden_substrings=forbidden_substrings,
        )


def _assert_breadcrumbs_are_metadata_only(
    breadcrumb_calls: list[dict[str, object]],
    *,
    forbidden_substrings: tuple[str, ...],
) -> None:
    allowed_breadcrumb_categories = {
        "ralph.command",
        "ralph.phase",
        "ralph.session",
    }
    for breadcrumb in breadcrumb_calls:
        assert breadcrumb.get("category") in allowed_breadcrumb_categories
        _assert_no_forbidden_substrings(
            _flatten(breadcrumb),
            source="breadcrumb",
            forbidden_substrings=forbidden_substrings,
        )


class _RecordingTransaction:
    def __init__(self, transaction_calls: list[tuple[str, str]]) -> None:
        self._transaction_calls = transaction_calls

    def finish(self) -> None:
        self._transaction_calls.append(("finish", ""))


def test_closed_vocabulary_privacy_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    tag_calls: list[tuple[str, object]] = []
    context_calls: list[tuple[str, dict[str, object]]] = []
    message_calls: list[tuple[str, object]] = []
    breadcrumb_calls: list[dict[str, object]] = []
    metric_count_calls: list[tuple[str, float, dict[str, object] | None]] = []
    metric_distribution_calls: list[tuple[str, float, str | None, dict[str, object] | None]] = []
    transaction_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "sentry_sdk.set_tag",
        lambda k, v: tag_calls.append((k, v)),
    )
    monkeypatch.setattr(
        "sentry_sdk.set_context",
        lambda name, data: context_calls.append((name, dict(data))),
    )
    monkeypatch.setattr(
        "sentry_sdk.capture_message",
        lambda msg, level=None: message_calls.append((msg, level)),
    )
    monkeypatch.setattr(
        "sentry_sdk.add_breadcrumb",
        lambda **kwargs: breadcrumb_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "sentry_sdk.start_session",
        lambda session_mode=None: None,
    )
    monkeypatch.setattr(
        "sentry_sdk.start_transaction",
        lambda op=None, name=None: (
            transaction_calls.append((str(op), str(name)))
            or _RecordingTransaction(transaction_calls)
        ),
    )
    monkeypatch.setattr(
        "sentry_sdk.end_session",
        lambda: None,
    )
    monkeypatch.setattr(
        _sentry.sentry_metrics,
        "count",
        lambda name, value, attributes=None: metric_count_calls.append(
            (name, value, dict(attributes) if attributes is not None else None)
        ),
    )
    monkeypatch.setattr(
        _sentry.sentry_metrics,
        "distribution",
        lambda name, value, unit=None, attributes=None: metric_distribution_calls.append(
            (
                name,
                value,
                unit,
                dict(attributes) if attributes is not None else None,
            )
        ),
    )
    monkeypatch.setattr("sentry_sdk.flush", lambda timeout=None: None)

    monkeypatch.setattr(_sentry, "_INITIALIZED", True)
    monkeypatch.setattr(_sentry, "_SESSION_STARTED_AT", None)
    monkeypatch.setattr(_sentry, "_SESSION_OUTCOME", "unknown")
    monkeypatch.setattr(_sentry, "_SESSION_WALLCLOCK_BUCKETS", None)
    monkeypatch.setattr(_sentry, "_PHASE_STATS", {})

    # Use ci=True/container=True so the privacy guard can verify bool-only.
    monkeypatch.setattr(
        _sentry,
        "current_platform",
        lambda: PlatformInfo(
            os=OperatingSystem.LINUX,
            architecture=Architecture.ARM64,
            environment=EnvironmentInfo(ci=True, container=True),
            package_manager="apt",
        ),
    )
    monkeypatch.setattr(_sentry, "ralph_version", "0.0.0-test")
    monkeypatch.setattr(
        _sentry.PythonVersionInfo,
        "from_sys",
        classmethod(
            lambda cls, sys_module: _version_info.PythonVersionInfo(
                major=3,
                minor=12,
                micro=5,
                releaselevel="final",
                serial=0,
                implementation="CPython",
                executable=Path("/usr/bin/python3.12"),
                version="3.12.5",
            )
        ),
    )

    class _FakeSysModule:
        prefix = "/opt/fake-venv"
        base_prefix = "/usr"
        exec_prefix = "/opt/fake-venv"
        base_exec_prefix = "/usr"
        executable = "/usr/bin/python3.12"

    monkeypatch.setattr(
        _sentry,
        "detect_runtime_environment",
        lambda env=None, sys_module=None: _make_runtime_env(
            in_virtualenv=True, fake_sys=_FakeSysModule()
        ),
    )

    _sentry.set_environment_context()

    # Exercise every new function with adversarial strings.
    _sentry.record_session_start(now=100.0)
    _sentry.record_command_invocation("pipeline")  # closed-vocabulary command
    _sentry.set_session_wallclock_start(now_dt=datetime(2026, 3, 12, 9, 30, tzinfo=UTC))

    # Unknown phase role MUST be dropped (privacy invariant).
    _sentry.record_phase_execution(role="secret-startup-phase", duration_s=1, outcome="success")
    assert _sentry._PHASE_STATS == {}

    _sentry.record_phase_execution(role="execution", duration_s=1, outcome="success")
    _sentry.record_phase_execution(role="review", duration_s=2, outcome="failure")

    _sentry.finalize_session(now=160.0, flush_timeout=2.0)

    # Invariant (a): no adversarial echo ("secret", "startup", "mvp").
    forbidden_substrings = ("secret", "startup", "mvp")
    for k, v in tag_calls:
        if isinstance(v, str):
            _assert_no_forbidden_substrings(
                [v],
                source=f"tag {k!r}",
                forbidden_substrings=forbidden_substrings,
            )
    for _ctx_name, payload in context_calls:
        _assert_no_forbidden_substrings(
            _flatten(payload),
            source="context payload",
            forbidden_substrings=forbidden_substrings,
        )
    for msg, _ in message_calls:
        if isinstance(msg, str):
            _assert_no_forbidden_substrings(
                [msg],
                source="message",
                forbidden_substrings=forbidden_substrings,
            )
    _assert_breadcrumbs_are_metadata_only(
        breadcrumb_calls,
        forbidden_substrings=forbidden_substrings,
    )
    _assert_metrics_are_metadata_only(
        metric_count_calls,
        metric_distribution_calls,
        forbidden_substrings=forbidden_substrings,
    )
    for op, name in transaction_calls:
        _assert_no_forbidden_substrings(
            [op, name],
            source="transaction",
            forbidden_substrings=forbidden_substrings,
        )

    # Invariant (b): every tag NAME is in the closed vocabulary.
    allowed_tag_names = {
        "command",
        "os",
        "architecture",
        "python_version",
        "ralph_version",
        "session_id",
        "ci",
        "container",
    }
    for k, _v in tag_calls:
        assert k in allowed_tag_names, f"unknown tag name leaked: {k!r}"

    # Invariant (c): ci/container tag VALUES are bool.
    tag_map = dict(tag_calls)
    if "ci" in tag_map:
        assert isinstance(tag_map["ci"], bool), f"ci tag is not bool: {tag_map['ci']!r}"
    if "container" in tag_map:
        assert isinstance(tag_map["container"], bool), (
            f"container tag is not bool: {tag_map['container']!r}"
        )

    # Invariant (d): phase context keys are all in PhaseRole closed vocabulary.
    session_payload = next((c[1] for c in context_calls if c[0] == "session"), None)
    assert session_payload is not None
    phases = session_payload.get("phases")
    if phases is not None:
        assert isinstance(phases, dict)
        phase_role_set = frozenset(get_args(PhaseRole))
        leaked_roles = set(phases.keys()) - phase_role_set
        assert not leaked_roles, f"phase context leaked unknown roles: {leaked_roles}"


def _flatten(value: object) -> list[object]:
    """Walk an arbitrary payload and yield every leaf value (depth-first)."""
    if isinstance(value, dict):
        out: list[object] = []
        for k, v in value.items():
            out.extend(_flatten(k))
            out.extend(_flatten(v))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_flatten(item))
        return out
    return [value]
