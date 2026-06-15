"""Tests for ralph.telemetry._user_identity."""

from __future__ import annotations

import configparser
import string
from typing import TYPE_CHECKING

from ralph.telemetry._user_identity import (
    _ALPHANUM,
    _CONFIG_FILENAME,
    _CONFIG_SECTION,
    _SESSION_ID_LENGTH,
    _USER_ID_KEY,
    _USER_ID_LENGTH,
    generate_session_id,
    get_or_create_user_id,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_creates_user_id_file_when_absent(tmp_path: Path) -> None:
    uid = get_or_create_user_id(tmp_path)
    assert (tmp_path / _CONFIG_FILENAME).exists()
    assert len(uid) == _USER_ID_LENGTH


def test_user_id_is_alphanumeric(tmp_path: Path) -> None:
    uid = get_or_create_user_id(tmp_path)
    assert all(c in _ALPHANUM for c in uid)


def test_user_id_is_persistent(tmp_path: Path) -> None:
    uid1 = get_or_create_user_id(tmp_path)
    uid2 = get_or_create_user_id(tmp_path)
    assert uid1 == uid2


def test_user_id_file_has_ini_section_and_key(tmp_path: Path) -> None:
    get_or_create_user_id(tmp_path)
    parser = configparser.ConfigParser()
    parser.read(tmp_path / _CONFIG_FILENAME, encoding="utf-8")
    assert parser.has_section(_CONFIG_SECTION)
    assert parser.has_option(_CONFIG_SECTION, _USER_ID_KEY)


def test_user_id_file_contains_explanatory_comment(tmp_path: Path) -> None:
    get_or_create_user_id(tmp_path)
    text = (tmp_path / _CONFIG_FILENAME).read_text(encoding="utf-8").lower()
    assert "personally identifiable" in text or "no personal" in text


def test_user_id_regenerated_if_wrong_length(tmp_path: Path) -> None:
    config_path = tmp_path / _CONFIG_FILENAME
    config_path.write_text(f"[{_CONFIG_SECTION}]\n{_USER_ID_KEY} = tooshort\n", encoding="utf-8")
    uid = get_or_create_user_id(tmp_path)
    assert len(uid) == _USER_ID_LENGTH


def test_user_id_regenerated_if_non_alphanumeric(tmp_path: Path) -> None:
    bad_id = "!" + "a" * 31
    config_path = tmp_path / _CONFIG_FILENAME
    config_path.write_text(f"[{_CONFIG_SECTION}]\n{_USER_ID_KEY} = {bad_id}\n", encoding="utf-8")
    uid = get_or_create_user_id(tmp_path)
    assert all(c in string.ascii_letters + string.digits for c in uid)


def test_generate_session_id_is_64_chars() -> None:
    sid = generate_session_id()
    assert len(sid) == _SESSION_ID_LENGTH


def test_generate_session_id_is_alphanumeric() -> None:
    sid = generate_session_id()
    assert all(c in _ALPHANUM for c in sid)


def test_generate_session_id_is_unique() -> None:
    sid1 = generate_session_id()
    sid2 = generate_session_id()
    assert sid1 != sid2


def test_get_or_create_uses_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg_dir = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    get_or_create_user_id()
    assert (xdg_dir / _CONFIG_FILENAME).exists()
