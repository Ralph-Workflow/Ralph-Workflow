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

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_an_escaping_reference_with_an_unrecognized_suffix_is_refused": (
        "an escaping reference is refused because it leaves the skill directory"
    ),
    "test_a_reference_without_a_file_extension_is_refused": (
        "a directed reference without an extension names a companion file"
    ),
    "test_a_directory_style_reference_is_refused": (
        "a directed directory reference names the companion files under it"
    ),
    "test_pure_fragment_and_external_link_destinations_are_permitted": (
        "the escaping link moved into its own refused test"
    ),
    "test_an_escaping_link_destination_is_refused": (
        "the escaping link moved out of the permitted test"
    ),
}

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

_CLAUSE_INITIAL_CUES: tuple[str, ...] = (
    "Read `notes` for details.",
    "- See `notes` first.",
    "**Note:** see `notes`.",
    "Done. Then check `notes`.",
    "(Refer to the `notes`.)",
)

_DESCRIPTIVE_CUES: tuple[str, ...] = (
    "The test read `notes` twice.",
    "We open `notes` before parsing.",
    "The hook will check `notes`.",
)

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


def test_an_unrecognized_suffix_link_destination_is_refused(sandbox: Path) -> None:
    raw = {name: dict(files) for name, files in _SKILL_FILES.items()}
    raw["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [notes](notes.raw).\n",
    }
    client, _calls = _client_for("v-test-1", raw)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/notes.raw" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_escaping_reference_with_an_unrecognized_suffix_is_refused(
    sandbox: Path,
) -> None:
    escaping = {name: dict(files) for name, files in _SKILL_FILES.items()}
    escaping["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `../../outside.raw`.\n",
    }
    client, _calls = _client_for("v-test-1", escaping)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" in outcome.preserved
    assert any("../../outside.raw" in message for message in outcome.messages)
    assert any("outside the skill directory" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_reference_without_a_file_extension_is_refused(sandbox: Path) -> None:
    bare = {name: dict(files) for name, files in _SKILL_FILES.items()}
    bare["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `notes` for the details.\n",
    }
    client, _calls = _client_for("v-test-1", bare)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" in outcome.preserved
    assert any("docs/notes" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_directory_style_reference_is_refused(sandbox: Path) -> None:
    directory = {name: dict(files) for name, files in _SKILL_FILES.items()}
    directory["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `assets/` for the details.\n",
    }
    client, _calls = _client_for("v-test-1", directory)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" in outcome.preserved
    assert any("docs/assets" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


@pytest.mark.parametrize("line", _CLAUSE_INITIAL_CUES)
def test_a_clause_initial_cue_names_a_companion_file(sandbox: Path, line: str) -> None:
    cued = {name: dict(files) for name, files in _SKILL_FILES.items()}
    cued["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": f"# Guide\n\n{line}\n".encode(),
    }
    client, _calls = _client_for("v-test-1", cued)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" in outcome.preserved
    assert any("docs/notes" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


@pytest.mark.parametrize("line", _DESCRIPTIVE_CUES)
def test_a_descriptive_cue_does_not_name_a_companion_file(sandbox: Path, line: str) -> None:
    described = {name: dict(files) for name, files in _SKILL_FILES.items()}
    described["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": f"# Guide\n\n{line}\n".encode(),
    }
    client, _calls = _client_for("v-test-1", described)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_code_identifier_or_version_token_is_permitted(sandbox: Path) -> None:
    identifiers = {name: dict(files) for name, files in _SKILL_FILES.items()}
    identifiers["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\nThe test read `context.tempDir` and the runtime is `python3.12`.\n"
        ),
    }
    client, _calls = _client_for("v-test-1", identifiers)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_deeply_nested_reference_that_is_carried_is_accepted(sandbox: Path) -> None:
    deep = {name: dict(files) for name, files in _SKILL_FILES.items()}
    deep["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/deep/guide.md": b"# Guide\n\nSee `a.md`.\n",
        "docs/deep/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", deep)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "deep" / "a.md").read_bytes() == (
        b"# Companion\n"
    )


def test_a_deeply_nested_reference_that_is_missing_is_refused(sandbox: Path) -> None:
    deep = {name: dict(files) for name, files in _SKILL_FILES.items()}
    deep["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/deep/guide.md": b"# Guide\n\nSee `a.md`.\n",
    }
    client, _calls = _client_for("v-test-1", deep)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/deep/a.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_reference_style_link_that_is_carried_is_accepted(sandbox: Path) -> None:
    styled = {name: dict(files) for name, files in _SKILL_FILES.items()}
    styled["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee the [companion][ref].\n\n[ref]: a.md\n",
        "docs/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", styled)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").read_bytes() == b"# Companion\n"


def test_a_reference_style_link_that_is_missing_is_refused(sandbox: Path) -> None:
    styled = {name: dict(files) for name, files in _SKILL_FILES.items()}
    styled["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee the [companion][ref].\n\n[ref]: missing.md\n",
    }
    client, _calls = _client_for("v-test-1", styled)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_with_a_non_file_url_scheme_is_permitted(sandbox: Path) -> None:
    external = {name: dict(files) for name, files in _SKILL_FILES.items()}
    external["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\n"
            b"[mail](mailto:someone@example.com), "
            b"[plain](http://example.com/doc.md), "
            b"and [ftp](ftp://example.com/doc.md).\n"
        ),
    }
    client, _calls = _client_for("v-test-1", external)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_skill_whose_companions_are_all_carried_installs(sandbox: Path) -> None:
    complete = {name: dict(files) for name, files in _SKILL_FILES.items()}
    complete["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `docs/guide.md`.\n",
        "docs/guide.md": b"# Guide\n\nSee `a.md` and `../shared/g.md`.\n",
        "docs/a.md": b"# Companion\n",
        "shared/g.md": b"# Shared\n",
    }
    client, _calls = _client_for("v-test-1", complete)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    installed = root / "systematic-debugging"
    assert (installed / "docs" / "a.md").read_bytes() == b"# Companion\n"
    assert (installed / "shared" / "g.md").read_bytes() == b"# Shared\n"


def test_a_nested_reference_with_fragment_is_accepted_when_present(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](a.md#section).\n",
        "docs/a.md": b"# Companion\n\n## Section\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").is_file()


def test_a_nested_reference_with_fragment_is_refused_when_missing(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](missing.md#section).\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_nested_reference_with_title_is_accepted_when_present(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [companion](a.md "Companion Title").\n',
        "docs/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").is_file()


def test_a_nested_reference_with_title_is_refused_when_missing(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [companion](missing.md "Companion Title").\n',
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_parent_relative_reference_with_fragment_is_accepted_when_present(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [shared](../shared/g.md#section).\n",
        "shared/g.md": b"# Shared\n\n## Section\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "shared" / "g.md").is_file()


def test_a_parent_relative_reference_with_fragment_is_refused_when_missing(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [shared](../shared/missing.md#section).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("shared/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_parent_relative_reference_with_title_is_accepted_when_present(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [shared](../shared/g.md "Shared Guide").\n',
        "shared/g.md": b"# Shared\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "shared" / "g.md").is_file()


def test_a_parent_relative_reference_with_title_is_refused_when_missing(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [shared](../shared/missing.md "Shared Guide").\n',
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("shared/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_pure_fragment_and_external_link_destinations_are_permitted(
    sandbox: Path,
) -> None:
    external = {name: dict(files) for name, files in _SKILL_FILES.items()}
    external["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\n"
            b"See [section](#section), "
            b"[empty fragment](#), "
            b'[external](https://example.com/doc.md#sec "Ext"), '
            b"and [absolute](/etc/x.md#sec 'Abs').\n"
        ),
    }
    client, _calls = _client_for("v-test-1", external)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_an_escaping_link_destination_is_refused(sandbox: Path) -> None:
    escaping = {name: dict(files) for name, files in _SKILL_FILES.items()}
    escaping["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [escaping](../../outside.md#sec "Esc").\n',
    }
    client, _calls = _client_for("v-test-1", escaping)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" in outcome.preserved
    assert any("../../outside.md" in message for message in outcome.messages)
    assert any("outside the skill directory" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_destination_with_an_uppercase_extension_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](missing.MD).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/missing.MD" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_destination_with_an_uppercase_extension_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](Companion.MD).\n",
        "docs/Companion.MD": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "Companion.MD").read_bytes() == (
        b"# Companion\n"
    )


def test_an_angle_bracket_destination_with_a_space_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](<missing file.md>).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/missing file.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_angle_bracket_destination_with_a_space_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](<missing file.md>).\n",
        "docs/missing file.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "missing file.md").read_bytes() == (
        b"# Companion\n"
    )


def test_a_percent_encoded_destination_that_is_missing_is_refused(sandbox: Path) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](missing%20file.md).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/missing file.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_percent_encoded_destination_that_is_carried_is_accepted(sandbox: Path) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](missing%20file.md).\n",
        "docs/missing file.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "missing file.md").read_bytes() == (
        b"# Companion\n"
    )


def test_a_percent_encoded_url_destination_stays_external(sandbox: Path) -> None:
    external = {name: dict(files) for name, files in _SKILL_FILES.items()}
    external["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [site](https%3A//example.com/doc.md).\n",
    }
    client, _calls = _client_for("v-test-1", external)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_percent_encoded_fragment_destination_is_not_a_file(sandbox: Path) -> None:
    fragment = {name: dict(files) for name, files in _SKILL_FILES.items()}
    fragment["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [section](%23section).\n",
    }
    client, _calls = _client_for("v-test-1", fragment)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_link_destination_to_a_nested_file_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](a.md).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/a.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_destination_to_a_nested_file_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](a.md).\n",
        "docs/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").read_bytes() == b"# Companion\n"


def test_a_link_destination_with_a_parent_relative_path_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [shared](../shared/g.md).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("shared/g.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_destination_with_a_parent_relative_path_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [shared](../shared/g.md).\n",
        "shared/g.md": b"# Shared\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "shared" / "g.md").read_bytes() == b"# Shared\n"


def test_a_link_definition_with_an_angle_bracket_destination_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (b"# Guide\n\nSee the [companion][ref].\n\n[ref]: <missing file.md>\n"),
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing file.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_bare_prose_mention_is_not_treated_as_a_file(sandbox: Path) -> None:
    prose = {name: dict(files) for name, files in _SKILL_FILES.items()}
    prose["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nThe file missing.md is not part of this skill.\n",
    }
    client, _calls = _client_for("v-test-1", prose)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_missing_markdown_reference_reports_that_the_archive_does_not_carry_it(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](a.md).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any(
        "docs/a.md" in message and "which the archive does not carry" in message
        for message in outcome.messages
    )
    assert not any("extension" in message for message in outcome.messages)


def test_the_carried_suffix_helper_agrees_with_the_allowed_suffix_allowlist() -> None:
    for suffix in upstream.ALLOWED_FILE_SUFFIXES:
        assert upstream._carried_suffix(f"docs/a{suffix}") == suffix.lower()
        assert upstream._carried_suffix(f"docs/a{suffix.upper()}") == suffix.lower()
    assert upstream._carried_suffix("docs/notes.raw") is None
    assert upstream._carried_suffix("docs/a") is None


def test_a_code_span_with_a_percent_encoded_space_is_refused_like_its_link_form(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `missing%20file.md`.\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing file.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_code_span_with_a_percent_encoded_space_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `missing%20file.md`.\n",
        "docs/missing file.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "missing file.md").read_bytes() == (
        b"# Companion\n"
    )


def test_a_code_span_with_a_literal_space_stays_a_snippet_and_is_permitted(
    sandbox: Path,
) -> None:
    snippet = {name: dict(files) for name, files in _SKILL_FILES.items()}
    snippet["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `missing file.md`.\n",
    }
    client, _calls = _client_for("v-test-1", snippet)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_code_span_that_names_a_reader_project_path_is_permitted(sandbox: Path) -> None:
    project = {name: dict(files) for name, files in _SKILL_FILES.items()}
    project["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nRun `npm test path/to/test.test.ts`.\n",
    }
    client, _calls = _client_for("v-test-1", project)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_an_uppercase_markdown_source_document_with_a_missing_target_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "GUIDE.MD": b"# Guide\n\nSee [companion](absent.md).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("absent.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_uppercase_markdown_source_document_with_a_carried_target_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "GUIDE.MD": b"# Guide\n\nSee [companion](absent.md).\n",
        "absent.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "absent.md").read_bytes() == b"# Companion\n"


def test_a_mixed_case_markdown_source_document_with_a_missing_target_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "GUIDE.Md": b"# Guide\n\nSee [companion](absent.md).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("absent.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_destination_with_a_mixed_case_extension_that_is_missing_is_refused(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](absent.Md).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/absent.Md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_destination_with_a_mixed_case_extension_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](Companion.Md).\n",
        "docs/Companion.Md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "Companion.Md").read_bytes() == (
        b"# Companion\n"
    )


def test_a_mixed_case_non_carried_identifier_stays_permitted(sandbox: Path) -> None:
    identifiers = {name: dict(files) for name, files in _SKILL_FILES.items()}
    identifiers["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\n"
            b"The test read `context.tempDir`, the flag is `config.DebugMode`, "
            b"and the runtime is `python3.12`.\n"
        ),
    }
    client, _calls = _client_for("v-test-1", identifiers)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()
