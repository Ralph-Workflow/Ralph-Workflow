from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.skills import upstream

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.skills.upstream import HttpClient, HttpResponse

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

_SKILL_FILES: dict[str, dict[str, bytes]] = {
    "test-driven-development": {
        "SKILL.md": b"# Test driven development\n\nWrite the failing test first.\n",
        "test-first.md": b"# Test first\n",
    },
    "systematic-debugging": {
        "SKILL.md": b"# Systematic debugging\n",
        "condition-based-waiting.md": (
            b"# Condition based waiting\n\nSee `condition-based-waiting-example.ts`.\n"
        ),
        "condition-based-waiting-example.ts": b"export const wait = (): boolean => true;\n",
    },
    "verification-before-completion": {
        "SKILL.md": b"# Verification before completion\n",
        "evidence-checklist.md": b"# Evidence checklist\n",
    },
}

_LOCATION_CUE_CASES: tuple[tuple[str, str, str], ...] = (
    ("Use `notes` in this directory.", "notes", "notes/x.txt"),
    ("Use `assets/` in this directory.", "assets", "assets/x.txt"),
    ("See `notes` in this directory.", "notes", "notes/x.txt"),
    ("The test read `notes` in this directory.", "notes", "notes/x.txt"),
)


def _release_body(tag: str) -> bytes:
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_bytes(tag: str, skills: Mapping[str, Mapping[str, bytes]]) -> bytes:
    root = f"superpowers-{tag.lstrip('v')}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for skill, files in skills.items():
            for relative, data in files.items():
                info = tarfile.TarInfo(f"{root}/skills/{skill}/{relative}")
                info.size = len(data)
                info.mode = 0o755 if relative.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _client_for(
    tag: str, skills: Mapping[str, Mapping[str, bytes]]
) -> tuple[HttpClient, list[str]]:
    calls: list[str] = []
    responses = {
        upstream.RELEASES_API_URL: (200, _release_body(tag)),
        upstream.ARCHIVE_URL_TEMPLATE.format(tag=tag): (
            200,
            _archive_bytes(tag, skills),
        ),
    }

    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        return responses.get(url, (404, b""))

    return (_get, calls)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


@pytest.mark.parametrize(("sentence", "target", "carried"), _LOCATION_CUE_CASES)
def test_a_location_cued_code_span_that_is_absent_is_refused(
    sandbox: Path, sentence: str, target: str, carried: str
) -> None:
    absent = {name: dict(files) for name, files in _SKILL_FILES.items()}
    absent["systematic-debugging"] = {
        "SKILL.md": f"# Systematic debugging\n\n{sentence}\n".encode(),
    }
    client, _calls = _client_for("v-test-1", absent)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any(target in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()
    assert not (root / "systematic-debugging" / carried).exists()


@pytest.mark.parametrize(("sentence", "target", "carried"), _LOCATION_CUE_CASES)
def test_a_location_cued_code_span_that_is_carried_installs(
    sandbox: Path, sentence: str, target: str, carried: str
) -> None:
    carried_payload = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried_payload["systematic-debugging"] = {
        "SKILL.md": f"# Systematic debugging\n\n{sentence}\n".encode(),
        carried: b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried_payload)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    assert not any("does not carry" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / carried).is_file()


def test_a_code_span_without_a_closing_backtick_is_not_a_location_cue(sandbox: Path) -> None:
    dangling = {name: dict(files) for name, files in _SKILL_FILES.items()}
    dangling["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nUse `notes in this directory.\n",
    }
    client, _calls = _client_for("v-test-1", dangling)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "SKILL.md").is_file()


def test_a_location_cue_that_does_not_follow_the_span_directly_is_ignored(
    sandbox: Path,
) -> None:
    indirect = {name: dict(files) for name, files in _SKILL_FILES.items()}
    indirect["systematic-debugging"] = {
        "SKILL.md": (
            b"# Systematic debugging\n\nUse `notes` and read the notes in this directory.\n"
        ),
    }
    client, _calls = _client_for("v-test-1", indirect)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "SKILL.md").is_file()


def test_a_location_cue_on_another_line_does_not_apply(sandbox: Path) -> None:
    elsewhere = {name: dict(files) for name, files in _SKILL_FILES.items()}
    elsewhere["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nUse `notes`\nin this directory.\n",
    }
    client, _calls = _client_for("v-test-1", elsewhere)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "SKILL.md").is_file()
