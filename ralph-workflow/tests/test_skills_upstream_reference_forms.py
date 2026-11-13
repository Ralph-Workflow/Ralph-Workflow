from __future__ import annotations

import io
import json
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


def test_an_html_attribute_reference_that_is_missing_is_refused(sandbox: Path) -> None:
    attributed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    attributed["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nRead <a href="missing.md">the guide</a>.\n',
    }
    client, _calls = _client_for("v-test-1", attributed)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_html_attribute_reference_that_is_carried_is_accepted(sandbox: Path) -> None:
    attributed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    attributed["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nRead <a href="companion.md">the companion</a>.\n',
        "docs/companion.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", attributed)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "companion.md").is_file()


def test_an_html_image_source_reference_that_is_missing_is_refused(sandbox: Path) -> None:
    attributed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    attributed["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nRead <img src="assets/diagram.txt"> the diagram.\n',
    }
    client, _calls = _client_for("v-test-1", attributed)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/assets/diagram.txt" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_html_image_source_reference_that_is_carried_is_accepted(sandbox: Path) -> None:
    attributed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    attributed["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nRead <img src="assets/diagram.txt"> the diagram.\n',
        "docs/assets/diagram.txt": b"diagram\n",
    }
    client, _calls = _client_for("v-test-1", attributed)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "assets" / "diagram.txt").is_file()


def test_an_escaping_html_attribute_reference_is_refused(sandbox: Path) -> None:
    attributed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    attributed["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nRead <a href="../../outside.md">the guide</a>.\n',
    }
    client, _calls = _client_for("v-test-1", attributed)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("../../outside.md" in message for message in outcome.messages)
    assert any("outside the skill directory" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_external_html_attribute_reference_is_permitted(sandbox: Path) -> None:
    attributed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    attributed["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\n"
            b'Read <a href="https://example.com/doc.md">the guide</a>, '
            b'<a href="#section">the section</a>, and '
            b'<a href="/etc/x.md">the local file</a>.\n'
        ),
    }
    client, _calls = _client_for("v-test-1", attributed)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_bare_html_tag_is_not_a_companion_reference(sandbox: Path) -> None:
    tagged = {name: dict(files) for name, files in _SKILL_FILES.items()}
    tagged["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\nThe probe returned `<T>` and the contract is `<Good>`, not `<Bad>`.\n"
        ),
    }
    client, _calls = _client_for("v-test-1", tagged)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_an_angle_bracket_link_with_parentheses_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    angled = {name: dict(files) for name, files in _SKILL_FILES.items()}
    angled["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [guide](<guide(with-parentheses).md>).\n",
    }
    client, _calls = _client_for("v-test-1", angled)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/guide(with-parentheses).md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_angle_bracket_link_with_parentheses_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    angled = {name: dict(files) for name, files in _SKILL_FILES.items()}
    angled["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [guide](<guide(with-parentheses).md>).\n",
        "docs/guide(with-parentheses).md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", angled)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide(with-parentheses).md").is_file()


def test_a_link_destination_with_nested_parentheses_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [guide](guide(with-parentheses).md).\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/guide(with-parentheses).md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_destination_with_nested_parentheses_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [guide](guide(with-parentheses).md).\n",
        "docs/guide(with-parentheses).md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide(with-parentheses).md").is_file()


def test_a_refused_html_attribute_reference_keeps_the_previous_installation(
    sandbox: Path,
) -> None:
    client, _calls = _client_for(
        "v-test-1", {name: dict(files) for name, files in _SKILL_FILES.items()}
    )
    first = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    assert first.status == "updated"
    root = upstream.canonical_skills_root()
    installed = root / "systematic-debugging" / "SKILL.md"
    before = installed.read_bytes()

    refused = {name: dict(files) for name, files in _SKILL_FILES.items()}
    refused["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nRead <a href="missing.md">the guide</a>.\n',
    }
    client, _calls = _client_for("v-test-2", refused)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing.md" in message for message in outcome.messages)
    assert installed.read_bytes() == before


def test_the_reference_shapes_of_the_published_release_install(sandbox: Path) -> None:
    shaped = {name: dict(files) for name, files in _SKILL_FILES.items()}
    shaped["test-driven-development"] = {
        "SKILL.md": (
            b"# Test driven development\n\nSee [writing good tests](writing-good-tests.md).\n"
        ),
        "writing-good-tests.md": b"# Writing good tests\n",
    }
    shaped["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "root-cause-tracing.md": (
            b"# Root cause tracing\n\n"
            b"See `root-cause-tracing.md` in this directory.\n\n"
            b"Use the bisection script `find-polluter.sh` in this directory.\n\n"
            b"**Symptom:** `.git` created in `packages/core/` (source code)\n"
        ),
        "find-polluter.sh": b"#!/usr/bin/env bash\n",
        "condition-based-waiting.md": (
            b"# Condition based waiting\n\n"
            b"See `condition-based-waiting-example.ts` in this directory.\n"
        ),
        "condition-based-waiting-example.ts": b"export const wait = (): boolean => true;\n",
    }
    client, _calls = _client_for("v-test-1", shaped)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" not in outcome.preserved
    root = upstream.canonical_skills_root()
    assert (root / "test-driven-development" / "writing-good-tests.md").is_file()
    assert (root / "systematic-debugging" / "root-cause-tracing.md").is_file()
    assert (root / "systematic-debugging" / "find-polluter.sh").is_file()
    assert (root / "systematic-debugging" / "condition-based-waiting-example.ts").is_file()
