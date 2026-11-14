from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.skills import upstream

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.skills.upstream import HttpClient, HttpResponse

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
_SUPERPOWERS = upstream.UNIVERSAL_UPSTREAMS[0]
_ANTI_SLOP = upstream.UNIVERSAL_UPSTREAMS[1]
_SUPERPOWERS_TAG = "v4.3.1"
_SUPERPOWERS_SKILLS: dict[str, dict[str, bytes]] = {
    "test-driven-development": {"SKILL.md": b"# Test driven development\n"},
    "systematic-debugging": {"SKILL.md": b"# Systematic debugging\n"},
    "verification-before-completion": {"SKILL.md": b"# Verification\n"},
}


def _archive(tag: str, root: str, skills: Mapping[str, Mapping[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for skill, files in skills.items():
            for relative, data in files.items():
                info = tarfile.TarInfo(f"{root}-{tag.lstrip('v')}/skills/{skill}/{relative}")
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _client_for(
    anti_slop_tag: str, anti_slop_files: Mapping[str, bytes]
) -> tuple[HttpClient, list[str]]:
    calls: list[str] = []
    responses: dict[str, HttpResponse] = {
        _SUPERPOWERS.api_url: (200, json.dumps({"tag_name": _SUPERPOWERS_TAG}).encode()),
        _SUPERPOWERS.archive_url_template.format(tag=_SUPERPOWERS_TAG): (
            200,
            _archive(_SUPERPOWERS_TAG, "superpowers", _SUPERPOWERS_SKILLS),
        ),
        _ANTI_SLOP.api_url: (200, json.dumps({"tag_name": anti_slop_tag}).encode()),
        _ANTI_SLOP.archive_url_template.format(tag=anti_slop_tag): (
            200,
            _archive(anti_slop_tag, "anti-slop", {"antislop": anti_slop_files}),
        ),
    }

    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        if url not in responses:
            raise AssertionError(f"unexpected non-fixture request: {url}")
        return responses[url]

    return _get, calls


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


def test_newer_antislop_release_updates_bytes_without_touching_project(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    monkeypatch.chdir(project)
    before = _tree_snapshot(project)
    old_files = {"SKILL.md": b"# Antislop 3.2.30\n", "VERSION": b"3.2.30\n"}
    old_client, old_calls = _client_for("v3.2.30", old_files)
    first = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=old_client)
    installed = upstream.canonical_skills_root() / "antislop"

    assert first.status == "updated"
    assert (installed / "SKILL.md").read_bytes() == old_files["SKILL.md"]
    assert (installed / "VERSION").read_bytes() == old_files["VERSION"]

    new_files = {"SKILL.md": b"# Antislop 3.2.31\n", "VERSION": b"3.2.31\n"}
    new_client, new_calls = _client_for("v3.2.31", new_files)
    second = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=new_client
    )

    assert second.status == "updated"
    assert "antislop" in second.installed
    assert (installed / "SKILL.md").read_bytes() == new_files["SKILL.md"]
    assert (installed / "VERSION").read_bytes() == new_files["VERSION"]
    assert _tree_snapshot(project) == before
    assert old_calls == [
        _SUPERPOWERS.api_url,
        _SUPERPOWERS.archive_url_template.format(tag=_SUPERPOWERS_TAG),
        _ANTI_SLOP.api_url,
        _ANTI_SLOP.archive_url_template.format(tag="v3.2.30"),
    ]
    assert new_calls == [
        _SUPERPOWERS.api_url,
        _ANTI_SLOP.api_url,
        _ANTI_SLOP.archive_url_template.format(tag="v3.2.31"),
    ]


def test_skill_cross_reference_is_not_a_missing_companion_but_a_file_reference_is(
    sandbox: Path,
) -> None:
    files = {
        "SKILL.md": (
            b"# Antislop\n\n"
            b"See `superpowers:test-driven-development` for test technique.\n"
            b"See `missing.md` in this directory for local rules.\n"
        ),
        "VERSION": b"3.2.30\n",
    }
    client, _calls = _client_for("v3.2.30", files)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "antislop" in outcome.preserved
    assert any("missing.md" in message for message in outcome.messages)
    assert not any("superpowers:test-driven-development" in message for message in outcome.messages)
    assert not (upstream.canonical_skills_root() / "antislop").exists()


def test_antislop_install_spawns_nothing_and_creates_no_project_or_plugin_files(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    monkeypatch.chdir(project)
    before = _tree_snapshot(project)

    def _spawn_refused(*args: object, **kwargs: object) -> object:
        raise AssertionError("the skill updater attempted to spawn a process")

    monkeypatch.setattr(subprocess, "run", _spawn_refused)
    monkeypatch.setattr(subprocess, "Popen", _spawn_refused)
    monkeypatch.setattr(os, "system", _spawn_refused)
    files = {"SKILL.md": b"# Antislop\n", "VERSION": b"3.2.30\n"}
    client, calls = _client_for("v3.2.30", files)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert _tree_snapshot(project) == before
    assert not (project / "CLAUDE.md").exists()
    assert not (project / "AGENTS.md").exists()
    for directory in ("plugins", ".plugins", "marketplace", ".marketplace"):
        assert not (project / directory).exists()
        assert not (sandbox / directory).exists()
    assert calls == [
        _SUPERPOWERS.api_url,
        _SUPERPOWERS.archive_url_template.format(tag=_SUPERPOWERS_TAG),
        _ANTI_SLOP.api_url,
        _ANTI_SLOP.archive_url_template.format(tag="v3.2.30"),
    ]
