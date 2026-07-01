"""Tests for ralph.telemetry._sentry."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from ralph.platform.architecture import Architecture
from ralph.platform.environment_info import EnvironmentInfo
from ralph.platform.models import PlatformInfo
from ralph.platform.operating_system import OperatingSystem
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
    assert kwargs.get("profiles_sample_rate") == 1.0


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


def test_scrub_event_preserves_relative_filename() -> None:
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
    assert frame["filename"] == "ralph/foo.py"


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

    context_calls: list[tuple[str, dict[str, object]]] = []
    message_calls: list[tuple[str, str]] = []
    flush_calls: list[float] = []

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

    _sentry.record_session_start(now=100.0)
    _sentry.set_session_outcome("success")
    duration = _sentry.finalize_session(now=160.0, flush_timeout=2.0)

    assert duration == pytest.approx(60.0)
    session_context = next(c for c in context_calls if c[0] == "session")
    assert session_context[1]["duration_s"] == pytest.approx(60.0)
    # Explicit timing markers — process-local monotonic values, no wall-clock leak.
    assert session_context[1]["started_monotonic_s"] == pytest.approx(100.0)
    assert session_context[1]["ended_monotonic_s"] == pytest.approx(160.0)
    assert session_context[1]["outcome"] == "success"
    assert len(message_calls) == 1
    assert message_calls[0][0] == "session end"
    assert message_calls[0][1] == "info"
    assert flush_calls == [2.0]


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
