from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ralph.skills import upstream
from tests.test_skills_upstream_install import (
    _NOW,
    _SKILL_FILES,
    _client_for,
)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


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
    hostile = {name: dict(files) for name, files in _SKILL_FILES.items()}
    hostile["antislop"] = {"SKILL.md": b"# Antislop\n", "LICENSE": b"MIT\n"}
    hostile_client, _calls = _client_for("v1", hostile)
    refused = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=hostile_client)
    assert refused.status == "failed"
    assert refused.error is not None and "refused extension" in refused.error
    assert not (upstream.canonical_skills_root() / "antislop" / "LICENSE").exists()

    valid = {name: dict(files) for name, files in _SKILL_FILES.items()}
    client, _calls = _client_for("v2", valid)
    accepted = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )
    assert accepted.status == "updated"
    assert (upstream.canonical_skills_root() / "antislop" / "VERSION").read_bytes() == b"3.2.30\n"
