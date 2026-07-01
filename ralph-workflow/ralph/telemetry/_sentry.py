"""Sentry initialization with privacy-compliant PII scrubbing.

Captures only anonymous metalevel telemetry: OS, architecture, runtime markers,
Python/Ralph version, virtualenv flag, session timing, and a coarse exit
outcome. The before_send scrubber redacts home-directory, cwd, and argv
prefixes and drops server_name + stack-frame abs_path so no codebase identity
leaves the process. RALPH_DISABLE_TELEMETRY=1 (or true/yes/on) skips
initialization at the single CLI chokepoint in ``_init_telemetry``.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import sentry_sdk

from ralph import __version__ as ralph_version
from ralph.platform.detection import current_platform
from ralph.runtime._version_info import PythonVersionInfo
from ralph.runtime.environment import detect_runtime_environment

if TYPE_CHECKING:
    from collections.abc import Mapping

_DSN: str = "https://418c4f0099a0db0987b420c3cd1d5bb0@o4511480216158208.ingest.de.sentry.io/4511480219959376"
_HOME_PREFIX: str = str(Path.home())

# Minimum length to qualify as a Windows drive-letter path: ``X:\``.
# Avoids matching ``C:foo`` (no separator after the colon).
_WINDOWS_DRIVE_LETTER_PREFIX_LEN: int = 3

_TRUE_DISABLE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})

# Module-level mutable accumulator compliant: tuple (immutable). Populated by
# init_sentry from os.getcwd() and path-like sys.argv entries.
_EXTRA_SCRUB_PREFIXES: tuple[str, ...] = ()

# Module-level scalars only (no list/dict/set/deque). Populated by the
# lifecycle functions; consumed by finalize_session.
_SESSION_STARTED_AT: float | None = None
_SESSION_OUTCOME: str = "unknown"
_INITIALIZED: bool = False


def is_telemetry_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when RALPH_DISABLE_TELEMETRY is set to a truthy value."""
    mapping = env if env is not None else os.environ
    raw = mapping.get("RALPH_DISABLE_TELEMETRY")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUE_DISABLE_VALUES


def _scrub_string(value: str) -> str:
    """Redact home, cwd, and argv prefixes from a string value.

    The first matching prefix wins (the order is _HOME_PREFIX first so that
    home directories nested inside argv/cwd prefixes still collapse to ``~``).
    Non-matching strings are returned verbatim.
    """
    if _HOME_PREFIX and _HOME_PREFIX in value:
        return value.replace(_HOME_PREFIX, "~")
    for prefix in _EXTRA_SCRUB_PREFIXES:
        if prefix and prefix in value:
            return value.replace(prefix, "<redacted>")
    return value


def _scrub_obj(obj: object) -> None:
    """Recursively replace home-directory and extra prefixes in all string values."""
    if isinstance(obj, dict):
        d = cast("dict[str, object]", obj)
        for key in list(d.keys()):
            val = d[key]
            if isinstance(val, str):
                d[key] = _scrub_string(val)
            else:
                _scrub_obj(val)
    elif isinstance(obj, list):
        lst = cast("list[object]", obj)
        for i, item in enumerate(lst):
            if isinstance(item, str):
                lst[i] = _scrub_string(item)
            else:
                _scrub_obj(item)


def _is_absolute_filename(value: object) -> bool:
    """Return True if ``value`` looks like an absolute filename on POSIX or Windows.

    POSIX: any path beginning with ``/``.
    Windows: drive-letter paths (``C:\\...`` or ``C:/...``) and UNC paths
    (starting with ``\\\\``). The cross-platform check is required so the
    scrubber redacts stack-frame basenames and argv prefixes on
    non-POSIX platforms too (AC-04 / AC-06).
    """
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/"):
        return True
    if len(value) >= _WINDOWS_DRIVE_LETTER_PREFIX_LEN and value[1] == ":" and value[0].isalpha():
        rest = value[2]
        return rest in ("\\", "/")
    return value.startswith("\\\\")


def _basename_of_absolute_path(filename: str) -> str:
    """Return the basename of an absolute POSIX or Windows path.

    POSIX (``/Users/jane/foo.py``) and Windows (``C:\\Users\\jane\\foo.py``,
    ``C:/Users/jane/foo.py``, ``\\\\server\\share\\foo.py``) absolute paths
    are all supported. The split uses ``\\`` first when present so UNC
    ``\\\\server\\share\\foo.py`` collapses correctly.
    """
    if "\\" in filename:
        return filename.rsplit("\\", 1)[-1]
    return Path(filename).name


def _scrub_frames(frames: object) -> None:
    if not isinstance(frames, list):
        return
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        d_frame = cast("dict[str, object]", frame)
        d_frame.pop("abs_path", None)
        filename = d_frame.get("filename")
        if _is_absolute_filename(filename):
            d_frame["filename"] = _basename_of_absolute_path(cast("str", filename))


def _scrub_event(event: object, _hint: object) -> object:
    if isinstance(event, dict):
        d = cast("dict[str, object]", event)
        d.pop("server_name", None)
        exception = d.get("exception")
        if isinstance(exception, dict):
            values = cast("dict[str, object]", exception).get("values")
            if isinstance(values, list):
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    stacktrace = cast("dict[str, object]", value).get("stacktrace")
                    if isinstance(stacktrace, dict):
                        _scrub_frames(cast("dict[str, object]", stacktrace).get("frames"))
    _scrub_obj(event)
    return event


def _build_extra_scrub_prefixes() -> tuple[str, ...]:
    """Collect cwd + path-like sys.argv entries into an immutable tuple of prefixes.

    Non-path strings (e.g. flag values, prompts, inline arguments) are filtered
    out by requiring an absolute-path prefix and a non-empty value. Empty
    strings are dropped. Order is preserved (cwd first, then argv) for
    deterministic scrubber output. The check is cross-platform: POSIX
    absolute (``/...``) and Windows absolute (``C:\\...``, ``C:/...``,
    ``\\\\server\\share\\...``) prefixes are all eligible.
    """
    prefixes: list[str] = []
    try:
        cwd = str(Path.cwd())
    except OSError:
        cwd = ""
    if cwd and cwd not in prefixes:
        prefixes.append(cwd)
    for entry in sys.argv:
        if not isinstance(entry, str) or not entry:
            continue
        if not _is_absolute_filename(entry):
            continue
        if entry not in prefixes:
            prefixes.append(entry)
    return tuple(prefixes)


def init_sentry(user_id: str, session_id: str) -> None:
    """Initialize Sentry with anonymous user identity and PII scrubbing."""
    global _EXTRA_SCRUB_PREFIXES, _INITIALIZED  # noqa: PLW0603
    _EXTRA_SCRUB_PREFIXES = _build_extra_scrub_prefixes()

    sentry_sdk.init(
        dsn=_DSN,
        send_default_pii=False,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        # Disable local-variable capture on stack frames: the scrubber can
        # only redact known prefixes (home/cwd/argv) so a local like
        # ``inline_prompt`` could otherwise be forwarded verbatim. We want
        # stack frames (file/line/function) but never their locals.
        include_local_variables=False,
        before_send=_scrub_event,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        before_send_transaction=_scrub_event,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    )
    sentry_sdk.set_user({"id": user_id})  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    sentry_sdk.set_tag("session_id", session_id)
    _INITIALIZED = True


def set_environment_context() -> None:
    """Attach anonymous runtime/OS context to Sentry.

    Sources ONLY privacy-safe metadata: OS + architecture + environment
    markers + package_manager from ``current_platform()``, Python
    major.minor.micro + implementation from ``PythonVersionInfo.from_sys``,
    Ralph ``__version__``, and the boolean ``in_virtualenv`` flag from
    ``detect_runtime_environment``. NEVER reads or forwards
    ``sys.executable``, ``sys.prefix/base_prefix/exec_prefix``,
    ``virtualenv_path``, or any ``os.environ`` value.
    """
    try:
        platform = current_platform()
        python_info = PythonVersionInfo.from_sys(sys)
        runtime_env = detect_runtime_environment()
        python_version = f"{python_info.major}.{python_info.minor}.{python_info.micro}"

        sentry_sdk.set_tag("os", platform.os.value)
        sentry_sdk.set_tag("architecture", platform.architecture.value)
        sentry_sdk.set_tag("python_version", python_version)
        sentry_sdk.set_tag("ralph_version", ralph_version)

        runtime_payload: dict[str, object] = {
            "python_implementation": python_info.implementation,
            "in_virtualenv": runtime_env.in_virtualenv,
            "environment_markers": platform.environment.markers(),
            "package_manager": platform.package_manager,
        }
        sentry_sdk.set_context("runtime", runtime_payload)
    except Exception:
        # Telemetry must never break the host process.
        pass


def record_session_start(now: float | None = None) -> None:
    """Record the monotonic-clock session start time.

    Tests inject ``now=<float>`` for determinism (audit_test_policy forbids
    ``time.monotonic()`` in test files). Production code passes ``now=None``
    so the call resolves to ``time.monotonic()`` at runtime.
    """
    global _SESSION_STARTED_AT  # noqa: PLW0603
    if now is None:
        now = time.monotonic()
    _SESSION_STARTED_AT = float(now)


def set_session_outcome(outcome: str) -> None:
    """Record the coarse session outcome: success / failure / interrupted / unknown."""
    global _SESSION_OUTCOME  # noqa: PLW0603
    _SESSION_OUTCOME = outcome


def flush_telemetry(timeout: float = 2.0) -> None:
    """Bounded, fail-soft Sentry flush."""
    with contextlib.suppress(Exception):
        sentry_sdk.flush(timeout=timeout)


def finalize_session(
    now: float | None = None,
    flush_timeout: float = 2.0,
) -> float | None:
    """Emit the session-end context + message and flush. Returns the duration in seconds.

    No-op (returns ``None``) when Sentry was never initialized or no session
    start was recorded — so tests that monkeypatch ``_init_telemetry`` to a
    no-op or skip the lifecycle do not flush real network I/O.

    The session context includes explicit start/end monotonic timing
    markers so the session timing payload is observable (matches the
    README's "Session timing (start, duration)" claim). These monotonic
    values are process-local: they are meaningful only inside this
    process instance and leak no real-world clock information.
    """
    if not _INITIALIZED or _SESSION_STARTED_AT is None:
        return None

    started = _SESSION_STARTED_AT
    end_clock = time.monotonic() if now is None else float(now)
    duration = max(0.0, end_clock - started)

    try:
        session_payload: dict[str, object] = {
            "duration_s": duration,
            "started_monotonic_s": started,
            "ended_monotonic_s": end_clock,
            "outcome": _SESSION_OUTCOME,
        }
        sentry_sdk.set_context("session", session_payload)
        sentry_sdk.capture_message("session end", level="info")
        flush_telemetry(flush_timeout)
    except Exception:
        pass
    return duration
