"""User identity helpers for anonymous telemetry — userId and sessionId generation."""

from __future__ import annotations

import configparser
import os
import secrets
import string
from pathlib import Path

_USER_ID_LENGTH = 32
_SESSION_ID_LENGTH = 64
_CONFIG_FILENAME = "ralph-workflow-user.ini"
_CONFIG_SECTION = "identity"
_USER_ID_KEY = "user_id"
_ALPHANUM: str = string.ascii_letters + string.digits


def _alphanum_token(length: int) -> str:
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def _config_dir_path(config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def get_or_create_user_id(config_dir: Path | None = None) -> str:
    """Return the persistent anonymous userId, creating it if needed."""
    base = _config_dir_path(config_dir)
    config_path = base / _CONFIG_FILENAME

    if config_path.is_file():
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
        if parser.has_option(_CONFIG_SECTION, _USER_ID_KEY):
            candidate = parser.get(_CONFIG_SECTION, _USER_ID_KEY)
            if len(candidate) == _USER_ID_LENGTH and all(c in _ALPHANUM for c in candidate):
                return candidate

    user_id = _alphanum_token(_USER_ID_LENGTH)
    _write_user_id_file(config_path, user_id)
    return user_id


def generate_session_id() -> str:
    """Return a fresh random sessionId for the current run."""
    return _alphanum_token(_SESSION_ID_LENGTH)


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
        "# lives at $XDG_CONFIG_HOME/ralph-workflow-user.ini when XDG_CONFIG_HOME\n"
        "# is set, otherwise at ~/.config/ralph-workflow-user.ini. Inspect whichever\n"
        "# path Ralph Workflow chose on this system to see what is stored.\n"
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
    config_path.write_text(content, encoding="utf-8")
