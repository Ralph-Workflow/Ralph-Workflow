from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts._path_file_backend import PathFileBackend
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.skills._content import (
    BASELINE_SKILL_NAMES,
    get_skill_content,
    get_skill_metadata,
    list_skill_names,
    materialize_skills_to_claude_dir,
    materialize_skills_to_dir,
)

pytestmark = pytest.mark.timeout_seconds(5)

if TYPE_CHECKING:
    from pathlib import Path


def _derived_bundle_skills() -> tuple[str, ...]:
    from importlib import import_module

    import_module("ralph.mcp.artifacts.markdown.specs")
    from ralph.mcp.artifacts.markdown.registry import registered_specs

    types = sorted({spec.artifact_type for spec in registered_specs()})
    return (
        "submit-artifact",
        *(f"submit-{artifact_type.replace('_', '-')}-artifact" for artifact_type in types),
    )


EXPECTED_BUNDLE_SKILLS: tuple[str, ...] = _derived_bundle_skills()

MANAGED_MARKER_NAME = ".ralph-managed.json"

UPSTREAM_PROVENANCE_KEYS: tuple[str, ...] = (
    "bundles",
    "mirrored_at",
    "skill_sources",
    "source_commit",
    "source_ref",
    "source_repo",
    "source_repos",
    "source_version",
)


class _CountingBackend(FileBackend):
    def __init__(self) -> None:
        self._delegate = PathFileBackend()
        self.writes = 0

    def exists(self, path: Path) -> bool:
        return self._delegate.exists(path)

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self._delegate.mkdir(path, parents=parents, exist_ok=exist_ok)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self._delegate.read_text(path, encoding=encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.writes += 1
        self._delegate.write_text(path, content, encoding=encoding)

    def replace(self, source: Path, destination: Path) -> None:
        self._delegate.replace(source, destination)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        self._delegate.unlink(path, missing_ok=missing_ok)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return self._delegate.glob(path, pattern)


def test_shipped_skill_set_is_exactly_the_artifact_skills() -> None:
    assert BASELINE_SKILL_NAMES == EXPECTED_BUNDLE_SKILLS
    assert list_skill_names() == EXPECTED_BUNDLE_SKILLS
    assert len(BASELINE_SKILL_NAMES) == len(EXPECTED_BUNDLE_SKILLS)


def test_shipped_skill_metadata_lists_exactly_the_artifact_skills() -> None:
    metadata = get_skill_metadata()
    assert tuple(metadata["skills"]) == EXPECTED_BUNDLE_SKILLS
    assert tuple(metadata["skills"]) == BASELINE_SKILL_NAMES


def test_shipped_bundle_metadata_carries_no_upstream_provenance() -> None:
    metadata = get_skill_metadata()
    for key in UPSTREAM_PROVENANCE_KEYS:
        assert key not in metadata, f"metadata.json must not carry {key!r}"
    assert set(metadata) == {"skills"}


def test_shipped_skill_directory_holds_only_the_artifact_skill_files() -> None:
    from importlib.resources import files

    content_dir = files("ralph.skills") / "content"
    shipped = sorted(entry.name for entry in content_dir.iterdir() if entry.name.endswith(".md"))
    assert shipped == sorted(f"{name}.md" for name in EXPECTED_BUNDLE_SKILLS)


def test_each_shipped_skill_has_substantial_content() -> None:
    for name in BASELINE_SKILL_NAMES:
        content = get_skill_content(name)
        assert content.startswith(f"---\nname: {name}\n"), (
            f"{name} must open with its own frontmatter name"
        )
        assert len(content.split()) >= 150, f"{name} shipped an empty body"


def test_get_skill_content_raises_for_unknown_name() -> None:
    with pytest.raises(ValueError):
        get_skill_content("definitely-not-a-real-skill")


@pytest.mark.parametrize(
    "retired_name",
    [
        "brainstorming",
        "writing-plans",
        "systematic-debugging",
        "open-design--frontend-design",
        "using-superpowers",
    ],
)
def test_get_skill_content_raises_for_a_retired_skill(retired_name: str) -> None:
    with pytest.raises(ValueError, match="Unknown baseline skill"):
        get_skill_content(retired_name)


def test_materialize_skills_to_claude_dir_writes_managed_marker_with_content_hash(
    tmp_path: Path,
) -> None:
    materialize_skills_to_claude_dir(tmp_path)
    first_name = BASELINE_SKILL_NAMES[0]
    marker_path = tmp_path / first_name / MANAGED_MARKER_NAME
    assert marker_path.exists()
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert "installed_content_sha256" in marker
    expected_sha = hashlib.sha256(get_skill_content(first_name).encode("utf-8")).hexdigest()
    assert marker["installed_content_sha256"] == expected_sha


def test_materialize_skills_to_claude_dir_skips_manually_edited_skill(
    tmp_path: Path,
) -> None:
    first_name = BASELINE_SKILL_NAMES[0]
    skill_dir = tmp_path / first_name
    skill_dir.mkdir(parents=True)
    original_sha = hashlib.sha256(b"# original content\n").hexdigest()
    (skill_dir / "SKILL.md").write_text("# original content\n", encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": first_name,
                "installed_content_sha256": original_sha,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("# user edited\n", encoding="utf-8")

    written = materialize_skills_to_claude_dir(tmp_path)

    assert first_name not in written
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# user edited\n"
    assert len(written) == len(BASELINE_SKILL_NAMES) - 1


def test_materialize_skills_to_dir_writes_all_skills(tmp_path: Path) -> None:
    written = materialize_skills_to_dir(tmp_path)
    assert written == list(BASELINE_SKILL_NAMES)
    written_files = sorted(p.name for p in tmp_path.glob("*.md"))
    assert written_files == sorted(f"{name}.md" for name in BASELINE_SKILL_NAMES)
    for name in BASELINE_SKILL_NAMES:
        path = tmp_path / f"{name}.md"
        assert path.read_text(encoding="utf-8").startswith(f"---\nname: {name}\n")


def test_materialize_skills_to_claude_dir_second_pass_writes_nothing(tmp_path: Path) -> None:
    backend = _CountingBackend()

    first_written = materialize_skills_to_claude_dir(tmp_path, backend=backend)
    first_contents = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert backend.writes > 0

    backend.writes = 0
    second_written = materialize_skills_to_claude_dir(tmp_path, backend=backend)

    assert backend.writes == 0
    assert second_written == first_written
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == first_contents


def test_materialize_skills_to_dir_second_pass_writes_nothing(tmp_path: Path) -> None:
    backend = _CountingBackend()

    first_written = materialize_skills_to_dir(tmp_path, backend=backend)
    first_contents = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert backend.writes > 0

    backend.writes = 0
    second_written = materialize_skills_to_dir(tmp_path, backend=backend)

    assert backend.writes == 0
    assert second_written == first_written
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == first_contents
