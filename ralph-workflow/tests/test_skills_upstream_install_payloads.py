from __future__ import annotations

import hashlib
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

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "RALPH_TEST_CHANGE_REASONS public archive seam": (
        "the extensionless-member assertions now use the public universal-skill update outcome and installed files instead of private archive internals"
    ),
    "test_version_companion_is_admitted_and_other_extensionless_members_rejected": (
        "the archive-admission checks now run through the public update seam ralph.skills.upstream.maybe_update_universal_skills instead of the private archive extractor, so this module asserts the shipped admission and refusal behaviour without reaching into private production internals"
    ),
}

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
    "antislop": {"SKILL.md": b"# Antislop\n\nFilter AI slop.\n", "VERSION": b"3.2.30\n"},
}


def _release_body(tag: str) -> bytes:
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_bytes(
    tag: str, skills: Mapping[str, Mapping[str, bytes]], *, root: str | None = None
) -> bytes:
    top = root if root is not None else f"superpowers-{tag.lstrip('v')}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for skill, files in skills.items():
            for relative, data in files.items():
                info = tarfile.TarInfo(f"{top}/skills/{skill}/{relative}")
                info.size = len(data)
                info.mode = 0o755 if relative.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _source_skills(
    skills: Mapping[str, Mapping[str, bytes]], source: upstream.UpstreamSource
) -> dict[str, Mapping[str, bytes]]:
    return {name: files for name, files in skills.items() if name in source.skill_names}


_ANTI_SLOP_TAG = "v3.2.30"


def _client_for(
    tag: str,
    skills: Mapping[str, Mapping[str, bytes]],
    *,
    anti_slop_tag: str = _ANTI_SLOP_TAG,
) -> tuple[HttpClient, list[str]]:
    calls: list[str] = []
    superpowers = upstream.UNIVERSAL_UPSTREAMS[0]
    anti_slop = upstream.UNIVERSAL_UPSTREAMS[1]
    responses = {
        upstream.RELEASES_API_URL: (200, _release_body(tag)),
        upstream.ARCHIVE_URL_TEMPLATE.format(tag=tag): (
            200,
            _archive_bytes(
                tag, _source_skills(skills, superpowers), root=f"superpowers-{tag.lstrip('v')}"
            ),
        ),
        anti_slop.api_url: (200, _release_body(anti_slop_tag)),
        anti_slop.archive_url_template.format(tag=anti_slop_tag): (
            200,
            _archive_bytes(
                anti_slop_tag,
                _source_skills(skills, anti_slop),
                root=f"anti-slop-{anti_slop_tag.lstrip('v')}",
            ),
        ),
    }

    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        return responses.get(url, (404, b""))

    return (_get, calls)


def _tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = "symlink:" + path.readlink().as_posix()
            continue
        if path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


def _install_fixture_release(tag: str = "v-test-1") -> Path:
    client, _calls = _client_for(tag, _SKILL_FILES)
    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    assert outcome.status == "updated"
    return upstream.canonical_skills_root()


def test_a_payload_without_a_skill_file_is_refused_and_keeps_the_install(sandbox: Path) -> None:
    root = _install_fixture_release()
    refused = root / "systematic-debugging"
    before = _tree_snapshot(refused)
    incomplete = {name: dict(files) for name, files in _SKILL_FILES.items()}
    incomplete["systematic-debugging"].pop("SKILL.md")
    client, _calls = _client_for("v-test-2", incomplete)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("SKILL.md" in message for message in outcome.messages)
    assert _tree_snapshot(refused) == before


def test_a_reference_that_matches_no_installed_relative_path_is_refused(sandbox: Path) -> None:
    mismatched = {name: dict(files) for name, files in _SKILL_FILES.items()}
    mismatched["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `docs/example.ts`.\n",
        "example.ts": b"export const example = 1;\n",
    }
    client, _calls = _client_for("v-test-1", mismatched)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/example.ts" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_reference_that_matches_the_exact_relative_path_is_accepted(sandbox: Path) -> None:
    exact = {name: dict(files) for name, files in _SKILL_FILES.items()}
    exact["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `docs/example.ts`.\n",
        "docs/example.ts": b"export const example = 1;\n",
    }
    client, _calls = _client_for("v-test-1", exact)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "example.ts").read_bytes() == (
        b"export const example = 1;\n"
    )


def test_a_nested_reference_resolves_against_the_referring_file(sandbox: Path) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `a.md`.\n",
        "docs/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").read_bytes() == b"# Companion\n"


def test_a_missing_in_tree_parent_relative_reference_is_refused(sandbox: Path) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `../shared/g.md`.\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("shared/g.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_in_tree_parent_relative_reference_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `../shared/g.md`.\n",
        "shared/g.md": b"# Shared\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "shared" / "g.md").read_bytes() == b"# Shared\n"


def test_a_root_relative_reference_that_is_missing_is_refused(sandbox: Path) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `README.md`.\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("README.md" in message for message in outcome.messages)


def test_a_refused_companion_reference_keeps_the_install(sandbox: Path) -> None:
    root = _install_fixture_release()
    refused = root / "systematic-debugging"
    before = _tree_snapshot(refused)
    escaping = {name: dict(files) for name, files in _SKILL_FILES.items()}
    escaping["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `../../outside.md`.\n",
    }
    client, _calls = _client_for("v-test-2", escaping)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert "systematic-debugging" in outcome.preserved
    assert any("outside the skill directory" in message for message in outcome.messages)
    assert _tree_snapshot(refused) == before


def test_an_escaping_reference_is_refused(sandbox: Path) -> None:
    escaping = {name: dict(files) for name, files in _SKILL_FILES.items()}
    escaping["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `../../outside.md`.\n",
    }
    client, _calls = _client_for("v-test-1", escaping)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" in outcome.preserved
    assert any("../../outside.md" in message for message in outcome.messages)
    assert any("outside the skill directory" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_external_references_are_permitted(sandbox: Path) -> None:
    external = {name: dict(files) for name, files in _SKILL_FILES.items()}
    external["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\nSee `/etc/x.md`, `~/y.md`, `https://example.com/doc.md` "
            b"and `docs/guide.md#section`.\n"
        ),
    }
    client, _calls = _client_for("v-test-1", external)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("See [n](notes).", "docs/notes"),
        ("See [a](assets/).", "docs/assets"),
        ("See [o](../../outside.md).", "../../outside.md"),
        ("See [d].\n\n[d]: ../x.md\n", "x.md"),
    ],
)
def test_a_link_reference_to_a_missing_companion_file_is_refused(
    sandbox: Path, line: str, expected: str
) -> None:
    linked = {name: dict(files) for name, files in _SKILL_FILES.items()}
    linked["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": f"# Guide\n\n{line}\n".encode(),
    }
    client, _calls = _client_for("v-test-1", linked)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert "systematic-debugging" in outcome.preserved
    assert any(expected in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_reference_to_a_carried_directory_is_accepted(sandbox: Path) -> None:
    linked = {name: dict(files) for name, files in _SKILL_FILES.items()}
    linked["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee [a](assets/).\n",
        "assets/x.txt": b"companion\n",
    }
    client, _calls = _client_for("v-test-1", linked)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "assets" / "x.txt").is_file()


@pytest.mark.parametrize(
    ("line", "carried"),
    [
        ("See `assets/` for the details.", "docs/assets/x.txt"),
        ("See `notes`", "docs/notes/x.txt"),
    ],
)
def test_a_directed_reference_to_a_carried_directory_is_accepted(
    sandbox: Path, line: str, carried: str
) -> None:
    directed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    directed["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": f"# Guide\n\n{line}\n".encode(),
        carried: b"companion\n",
    }
    client, _calls = _client_for("v-test-1", directed)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / carried).is_file()


def test_an_uncued_worked_example_reference_is_permitted(sandbox: Path) -> None:
    worked = {name: dict(files) for name, files in _SKILL_FILES.items()}
    worked["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\n**Symptom:** `.git` created in `packages/core/` (source code)\n"
        ),
    }
    client, _calls = _client_for("v-test-1", worked)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_the_upstream_release_reference_shapes_are_permitted(sandbox: Path) -> None:
    release = {name: dict(files) for name, files in _SKILL_FILES.items()}
    release["test-driven-development"] = {
        "SKILL.md": (
            b"# Test driven development\n\n"
            b"read [writing-good-tests.md](writing-good-tests.md) for the rules.\n"
        ),
        "writing-good-tests.md": b"# Writing good tests\n",
    }
    release["systematic-debugging"] = {
        "SKILL.md": (
            b"# Systematic debugging\n\n"
            b"See `root-cause-tracing.md` in this directory for the technique.\n"
        ),
        "root-cause-tracing.md": (
            b"# Root cause tracing\n\n"
            b"Use the bisection script `find-polluter.sh` in this directory: "
            b"bash ./find-polluter.sh\n\n"
            b"**Symptom:** `.git` created in `packages/core/` (source code)\n\n"
            b"4. Test accessed `context.tempDir` before beforeEach\n"
        ),
        "find-polluter.sh": b"#!/bin/sh\nexit 0\n",
        "condition-based-waiting.md": (
            b"# Condition based waiting\n\n"
            b"See `condition-based-waiting-example.ts` in this directory for the helper.\n"
        ),
        "condition-based-waiting-example.ts": b"export const wait = true;\n",
    }
    client, _calls = _client_for("v-test-1", release)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "test-driven-development" / "writing-good-tests.md").is_file()
    assert (root / "systematic-debugging" / "root-cause-tracing.md").is_file()
    assert (root / "systematic-debugging" / "find-polluter.sh").is_file()
    assert (root / "systematic-debugging" / "condition-based-waiting-example.ts").is_file()


def test_an_unrecognized_suffix_reference_is_refused(sandbox: Path) -> None:
    raw = {name: dict(files) for name, files in _SKILL_FILES.items()}
    raw["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `notes.raw`.\n",
    }
    client, _calls = _client_for("v-test-1", raw)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/notes.raw" in message for message in outcome.messages)
    assert any(
        "docs/notes.raw" in message and "extension" in message for message in outcome.messages
    )
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_directed_code_span_with_a_literal_space_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `missing file.md`.\n",
        "docs/missing file.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "missing file.md").read_bytes() == (
        b"# Companion\n"
    )


def test_version_companion_is_admitted_and_other_extensionless_members_rejected(
    sandbox: Path,
) -> None:
    valid_client, _valid_calls = _client_for("v-test-1", _SKILL_FILES)

    installed = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=valid_client)

    assert installed.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "antislop" / "VERSION").read_bytes() == b"3.2.30\n"

    hostile_skills = {name: dict(files) for name, files in _SKILL_FILES.items()}
    hostile_skills["antislop"] = {"SKILL.md": b"# Antislop\n", "LICENSE": b"MIT\n"}
    hostile_client, _hostile_calls = _client_for(
        "v-test-2", hostile_skills, anti_slop_tag="v3.2.31"
    )

    refused = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=hostile_client
    )

    assert refused.status == "failed"
    assert refused.error is not None
    assert "refused extension" in refused.error
