"""User identity helpers for anonymous telemetry — userId and sessionId generation."""

from __future__ import annotations

import configparser
import contextlib
import os
import secrets
import string
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_USER_ID_LENGTH = 32
_SESSION_ID_LENGTH = 64
_CONFIG_FILENAME = "ralph-workflow-user.ini"
_CONFIG_SECTION = "identity"
_USER_ID_KEY = "user_id"
_ALPHANUM: str = string.ascii_letters + string.digits
_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT_SECONDS = 0.5
_LOCK_POLL_SECONDS = 0.01
_LOCK_STALE_SECONDS = 30.0


def _alphanum_token(length: int) -> str:
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def _config_dir_path(config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir
    return Path.home() / ".config"


def _legacy_config_path(canonical_path: Path) -> Path | None:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if not xdg:
        return None
    candidate = Path(xdg) / _CONFIG_FILENAME
    return candidate if candidate != canonical_path else None


def _read_valid_user_id(config_path: Path) -> str | None:
    if not config_path.is_file():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
        candidate = parser.get(_CONFIG_SECTION, _USER_ID_KEY, fallback="")
    except (configparser.Error, OSError):
        return None
    if len(candidate) != _USER_ID_LENGTH or not all(c in _ALPHANUM for c in candidate):
        return None
    return candidate


def get_or_create_user_id(config_dir: Path | None = None) -> str:
    """Return the persistent anonymous user ID, creating it if needed.

    The default location is independent of ``XDG_CONFIG_HOME`` so separate
    terminal environments share one identity. A valid legacy XDG file is
    migrated to the canonical location on first use.
    """
    base = _config_dir_path(config_dir)
    config_path = base / _CONFIG_FILENAME
    legacy_path = _legacy_config_path(config_path)

    existing = _read_valid_user_id(config_path)
    if existing is not None:
        return existing

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with _identity_lock(config_path.with_name(config_path.name + _LOCK_SUFFIX)):
        existing = _read_valid_user_id(config_path)
        if existing is not None:
            return existing
        legacy = _read_valid_user_id(legacy_path) if legacy_path is not None else None
        user_id = legacy or _alphanum_token(_USER_ID_LENGTH)
        _write_user_id_file(config_path, user_id)
        return user_id


def generate_session_id() -> str:
    """Return a fresh random sessionId for the current run."""
    return _alphanum_token(_SESSION_ID_LENGTH)


@contextmanager
def _identity_lock(lock_path: Path) -> Iterator[None]:
    """Acquire a short-lived cross-process lock for identity publication."""
    started = time.monotonic()
    lock_fd: int | None = None
    while lock_fd is None:
        try:
            lock_fd = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(lock_fd)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age >= _LOCK_STALE_SECONDS:
                with contextlib.suppress(OSError):
                    lock_path.unlink()
                continue
            if time.monotonic() - started >= _LOCK_TIMEOUT_SECONDS:
                raise OSError("timed out acquiring telemetry identity lock") from None
            time.sleep(_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


def _write_user_id_file(config_path: Path, user_id: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Ralph Workflow — anonymous user identity file\n"
        "#\n"
        "# This file stores a randomly generated identifier used solely to\n"
        "# distinguish different installations from one another in anonymous\n"
        "# session-health, performance, and usage metadata sent to our telemetry\n"
        "# service (Sentry, EU-hosted).\n"
        "#\n"
        "# This value contains NO personally identifiable information.\n"
        "# It is not tied to your name, email, IP address, or any other\n"
        "# personal data. It is a random alphanumeric string — nothing more.\n"
        "#\n"
        "# Legal basis: legitimate interest (GDPR Art. 6(1)(f)) and the\n"
        "# California Consumer Privacy Act (CCPA) exemption for data that\n"
        "# cannot reasonably identify a natural person.\n"
        "#\n"
        "# Ralph Workflow reports anonymous metadata-only telemetry to improve\n"
        "# app quality, understand feature adoption and active usage, and inform\n"
        "# users about useful product capabilities. No personal data is ever sent,\n"
        "# and the data is never sold, rented, or shared for advertising. The file\n"
        "# lives at ~/.config/ralph-workflow-user.ini regardless of\n"
        "# XDG_CONFIG_HOME, so separate terminal applications share one ID. A\n"
        "# valid older XDG_CONFIG_HOME file is migrated here on first use. Inspect\n"
        "# this path to see what is stored.\n"
        "#\n"
        "# To opt out: set telemetry_enabled = false in your user-global or\n"
        "# project-local ralph-workflow.toml, or set RALPH_DISABLE_TELEMETRY to\n"
        "# a truthy value (any of 1/true/yes/on, case-insensitive) before invoking\n"
        "# Ralph Workflow. This file is NOT an opt-out on its own: if it is missing,\n"
        "# Ralph Workflow will create a new random ID on the next run only when\n"
        "# telemetry is enabled.\n"
        "\n"
        f"[{_CONFIG_SECTION}]\n"
        f"{_USER_ID_KEY} = {user_id}\n"
    )
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.",
        dir=config_path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        if os.name != "nt":
            temp_path.chmod(0o600)
        with os.fdopen(temp_fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.replace(config_path)
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink()
