from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ralph.agents.agy_auth_vault import (
    AGY_OAUTH_FILE,
    agy_auth_vault_dir,
    prepare_agy_auth_home,
    project_agy_auth,
    write_back_agy_auth,
)

if TYPE_CHECKING:
    from pathlib import Path


def _agy_dir(home: Path) -> Path:
    path = home / ".gemini" / "antigravity-cli"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, text: str, *, mtime: float) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_vault_lives_under_the_user_data_home(tmp_path: Path) -> None:
    assert agy_auth_vault_dir({"HOME": str(tmp_path)}) == (
        tmp_path / ".local" / "share" / "ralph-workflow" / "agy-auth"
    )
    assert agy_auth_vault_dir({"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "d")}) == (
        tmp_path / "d" / "ralph-workflow" / "agy-auth"
    )


def test_a_token_in_the_agy_home_is_captured_into_the_vault_and_projected(tmp_path: Path) -> None:
    source = _agy_dir(tmp_path / "home")
    vault = tmp_path / "vault"
    _write(source / AGY_OAUTH_FILE, "token-1", mtime=1000.0)
    destination = tmp_path / "private"

    project_agy_auth(source, destination, vault)

    assert (vault / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "token-1"
    assert (destination / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "token-1"


def test_the_vault_keeps_agents_signed_in_after_the_agy_home_token_disappears(
    tmp_path: Path,
) -> None:
    source = _agy_dir(tmp_path / "home")
    vault = tmp_path / "vault"
    _write(source / AGY_OAUTH_FILE, "token-1", mtime=1000.0)
    project_agy_auth(source, tmp_path / "first", vault)
    (source / AGY_OAUTH_FILE).unlink()

    project_agy_auth(source, tmp_path / "second", vault)

    assert (tmp_path / "second" / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "token-1"


def test_a_newer_sign_in_in_the_agy_home_replaces_the_vaulted_token(tmp_path: Path) -> None:
    source = _agy_dir(tmp_path / "home")
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(vault / AGY_OAUTH_FILE, "old", mtime=1000.0)
    _write(source / AGY_OAUTH_FILE, "fresh-sign-in", mtime=2000.0)

    project_agy_auth(source, tmp_path / "private", vault)

    assert (vault / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "fresh-sign-in"
    assert (tmp_path / "private" / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "fresh-sign-in"


def test_a_stale_agy_home_token_never_overrides_a_newer_vaulted_refresh(tmp_path: Path) -> None:
    source = _agy_dir(tmp_path / "home")
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(source / AGY_OAUTH_FILE, "stale", mtime=1000.0)
    _write(vault / AGY_OAUTH_FILE, "refreshed", mtime=2000.0)

    project_agy_auth(source, tmp_path / "private", vault)

    assert (tmp_path / "private" / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "refreshed"
    assert (vault / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "refreshed"


def test_a_token_refreshed_inside_a_private_home_is_written_back_to_the_vault(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(vault / AGY_OAUTH_FILE, "before-refresh", mtime=1000.0)
    private = tmp_path / "private"
    private.mkdir()
    _write(private / AGY_OAUTH_FILE, "after-refresh", mtime=2000.0)

    write_back_agy_auth(private, vault)

    assert (vault / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "after-refresh"
    assert (vault / AGY_OAUTH_FILE).stat().st_mode & 0o077 == 0


def test_write_back_never_replaces_the_vault_with_an_older_token(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(vault / AGY_OAUTH_FILE, "newest", mtime=3000.0)
    private = tmp_path / "private"
    private.mkdir()
    _write(private / AGY_OAUTH_FILE, "older", mtime=1000.0)

    write_back_agy_auth(private, vault)

    assert (vault / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "newest"


def test_an_auth_home_projects_the_vault_and_writes_back_on_cleanup(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = _agy_dir(home)
    _write(source / AGY_OAUTH_FILE, "token-1", mtime=1000.0)
    _write(source / "settings.json", '{"model": "Gemini 4 Argon (Low)"}', mtime=1000.0)
    env = {"HOME": str(home), "XDG_DATA_HOME": str(tmp_path / "data")}

    private_home, cleanup = prepare_agy_auth_home(env)
    private_agy = private_home / ".gemini" / "antigravity-cli"
    assert private_home != home
    assert (private_agy / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "token-1"
    assert (private_agy / "settings.json").is_file()
    _write(private_agy / AGY_OAUTH_FILE, "token-2", mtime=5000.0)

    cleanup()

    assert not private_home.exists()
    vault = agy_auth_vault_dir(env)
    assert (vault / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "token-2"
    assert (source / AGY_OAUTH_FILE).read_text(encoding="utf-8") == "token-1"
