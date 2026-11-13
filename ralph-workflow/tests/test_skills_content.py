from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final

import pytest

from ralph.mcp.artifacts._path_file_backend import PathFileBackend
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.skills._content import (
    BASELINE_SKILL_NAMES,
    get_skill_content,
    materialize_skills_to_claude_dir,
    materialize_skills_to_dir,
)

RALPH_TEST_CHANGE_REASONS: Final[dict[str, str]] = {
    "test_shipped_bundle_metadata_carries_no_upstream_provenance": (
        "the packaged metadata manifest was removed; installed metadata is generated "
        "from the packaged skill content"
    ),
    "test_shipped_skill_directory_holds_only_the_artifact_skill_files": (
        "the artifact-skill audit is the single drift authority for the packaged skill set"
    ),
    "test_materialized_metadata_lists_exactly_the_installed_skill_names": (
        "installed metadata is generated from the packaged skill content and is verified "
        "against the materialized SKILL.md files"
    ),
}

pytestmark = pytest.mark.timeout_seconds(5)

if TYPE_CHECKING:
    from pathlib import Path


MANAGED_MARKER_NAME = ".ralph-managed.json"


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


def test_materialized_metadata_lists_exactly_the_installed_skill_names(tmp_path: Path) -> None:
    materialize_skills_to_claude_dir(tmp_path)

    payload = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    installed_names = sorted(path.parent.name for path in tmp_path.glob("*/SKILL.md"))

    assert payload == {"skills": installed_names}


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


def test_materialize_skills_to_claude_dir_preserves_a_skill_with_unknown_ownership(
    tmp_path: Path,
) -> None:
    name = BASELINE_SKILL_NAMES[0]
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    skill_body = b"# my own version of the skill\n"
    marker_body = b"{ this is not valid json\n"
    (skill_dir / "SKILL.md").write_bytes(skill_body)
    (skill_dir / MANAGED_MARKER_NAME).write_bytes(marker_body)

    written = materialize_skills_to_claude_dir(tmp_path)

    assert name not in written
    assert (skill_dir / "SKILL.md").read_bytes() == skill_body, (
        "an unknown-ownership skill must survive materialization byte-for-byte"
    )
    assert (skill_dir / MANAGED_MARKER_NAME).read_bytes() == marker_body, (
        "an unknown-ownership marker must survive materialization byte-for-byte"
    )


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


def _plant_managed_skill(skill_dir: Path, name: str, content: str) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir


def test_materialize_skills_to_claude_dir_preserves_a_managed_skill_carrying_a_user_file(
    tmp_path: Path,
) -> None:
    name = BASELINE_SKILL_NAMES[0]
    skill_dir = tmp_path / name
    installed = "# the bundled version Ralph installed earlier\n"
    _plant_managed_skill(skill_dir, name, installed)
    user_file = skill_dir / "notes.md"
    user_file.write_text("# my notes\n", encoding="utf-8")

    written = materialize_skills_to_claude_dir(tmp_path)

    assert name not in written
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == installed
    assert user_file.read_text(encoding="utf-8") == "# my notes\n"


def test_materialize_skills_to_claude_dir_preserves_a_managed_skill_carrying_an_unrecorded_symlink(
    tmp_path: Path,
) -> None:
    name = BASELINE_SKILL_NAMES[0]
    skill_dir = tmp_path / name
    installed = "# the bundled version Ralph installed earlier\n"
    _plant_managed_skill(skill_dir, name, installed)
    user_target = tmp_path / "user-notes.md"
    user_target.write_text("# my notes\n", encoding="utf-8")
    link = skill_dir / "my-notes.md"
    link.symlink_to(user_target)

    written = materialize_skills_to_claude_dir(tmp_path)

    assert name not in written
    assert link.is_symlink()
    assert user_target.read_text(encoding="utf-8") == "# my notes\n"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == installed


def test_materialize_skills_to_claude_dir_never_writes_through_a_symlinked_skill_directory(
    tmp_path: Path,
) -> None:
    name = BASELINE_SKILL_NAMES[0]
    user_dir = tmp_path / "user-skill"
    user_dir.mkdir()
    (user_dir / "SKILL.md").write_text("# my own skill\n", encoding="utf-8")
    link = tmp_path / name
    link.symlink_to(user_dir, target_is_directory=True)

    written = materialize_skills_to_claude_dir(tmp_path)

    assert name not in written
    assert link.is_symlink()
    assert (user_dir / "SKILL.md").read_text(encoding="utf-8") == "# my own skill\n"
    assert not (user_dir / MANAGED_MARKER_NAME).exists()


def test_materialize_skills_to_claude_dir_refreshes_a_managed_skill_when_the_bundle_changed(
    tmp_path: Path,
) -> None:
    name = BASELINE_SKILL_NAMES[0]
    skill_dir = tmp_path / name
    installed = "# the bundled version Ralph installed earlier\n"
    _plant_managed_skill(skill_dir, name, installed)

    written = materialize_skills_to_claude_dir(tmp_path)

    assert name in written
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == get_skill_content(name)
    marker = json.loads((skill_dir / MANAGED_MARKER_NAME).read_text(encoding="utf-8"))
    expected_sha = hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()
    assert marker["installed_content_sha256"] == expected_sha


def test_materialize_skills_to_claude_dir_installs_a_missing_skill_directory(
    tmp_path: Path,
) -> None:
    written = materialize_skills_to_claude_dir(tmp_path)

    assert written == list(BASELINE_SKILL_NAMES)
    for name in BASELINE_SKILL_NAMES:
        skill_dir = tmp_path / name
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == get_skill_content(name)
        assert (skill_dir / MANAGED_MARKER_NAME).exists()


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
