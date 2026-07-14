"""Tests for ralph.telemetry._user_identity."""

from __future__ import annotations

import configparser
import string
from concurrent.futures import ThreadPoolExecutor
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


def test_get_or_create_uses_canonical_home_config_when_xdg_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    xdg_dir = tmp_path / "xdg"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    get_or_create_user_id()
    assert (home / ".config" / _CONFIG_FILENAME).exists()
    assert not (xdg_dir / _CONFIG_FILENAME).exists()


def test_user_id_survives_switching_terminal_xdg_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Terminal-specific XDG directories must not split one user identity."""
    home = tmp_path / "home"
    terminal_one_xdg = tmp_path / "terminal-one"
    terminal_two_xdg = tmp_path / "terminal-two"
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.setenv("XDG_CONFIG_HOME", str(terminal_one_xdg))
    first = get_or_create_user_id()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(terminal_two_xdg))
    second = get_or_create_user_id()

    assert second == first


def test_valid_legacy_xdg_identity_is_migrated_to_canonical_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing XDG identity is preserved when the canonical path changes."""
    home = tmp_path / "home"
    legacy_xdg = tmp_path / "legacy-xdg"
    legacy_xdg.mkdir()
    expected = "a" * _USER_ID_LENGTH
    (legacy_xdg / _CONFIG_FILENAME).write_text(
        f"[{_CONFIG_SECTION}]\n{_USER_ID_KEY} = {expected}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(legacy_xdg))

    actual = get_or_create_user_id()

    assert actual == expected
    canonical_path = home / ".config" / _CONFIG_FILENAME
    assert canonical_path.exists()
    parser = configparser.ConfigParser()
    parser.read(canonical_path, encoding="utf-8")
    assert parser.get(_CONFIG_SECTION, _USER_ID_KEY) == expected


def test_canonical_identity_wins_over_conflicting_legacy_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical = tmp_path / "canonical"
    legacy_xdg = tmp_path / "legacy-xdg"
    canonical_id = get_or_create_user_id(canonical)
    legacy_xdg.mkdir()
    legacy_id = "b" * _USER_ID_LENGTH
    (legacy_xdg / _CONFIG_FILENAME).write_text(
        f"[{_CONFIG_SECTION}]\n{_USER_ID_KEY} = {legacy_id}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(legacy_xdg))

    assert get_or_create_user_id(canonical) == canonical_id


def test_concurrent_first_use_returns_one_persisted_identity(tmp_path: Path) -> None:
    """Concurrent terminal starts must converge on one persisted identifier."""

    def create_identity() -> str:
        return get_or_create_user_id(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _index: create_identity(), range(8)))

    assert len(set(values)) == 1
    parser = configparser.ConfigParser()
    parser.read(tmp_path / _CONFIG_FILENAME, encoding="utf-8")
    assert parser.get(_CONFIG_SECTION, _USER_ID_KEY) == values[0]
