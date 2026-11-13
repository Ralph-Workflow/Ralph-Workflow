from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from ralph.mcp.artifacts.format_docs import FORMAT_DOC_ARTIFACT_TYPES
from ralph.mcp.artifacts.markdown.registry import registered_specs
from ralph.testing.audit_artifact_skill_types import audit_artifact_skill_types, main

RALPH_TEST_CHANGE_REASONS: Final[dict[str, str]] = {
    "test_derived_skill_set_matches_the_shipped_filenames_and_metadata": (
        "the packaged metadata manifest was removed; the audit compares the packaged "
        "skill files directly with the registered artifact types"
    ),
    "test_metadata_with_an_extra_name_is_reported": (
        "the packaged metadata manifest was removed; installed metadata is generated "
        "from the packaged skill content"
    ),
    "test_metadata_with_a_missing_name_is_reported": (
        "the packaged metadata manifest was removed; installed metadata is generated "
        "from the packaged skill content"
    ),
    "test_metadata_with_a_duplicate_name_is_reported": (
        "the packaged metadata manifest was removed; installed metadata is generated "
        "from the packaged skill content"
    ),
}

if TYPE_CHECKING:
    from collections.abc import Iterator

_CONTENT_DIR = Path(__file__).resolve().parents[1] / "ralph" / "skills" / "content"

_FORMAT_DOC_DIR = (
    Path(__file__).resolve().parents[1] / "ralph" / "mcp" / "artifacts" / "format_docs"
)

_ARTIFACT_TYPE_RE = re.compile(r'("?artifact_type"?:\s*)"?[A-Za-z0-9_]+"?')


def _artifact_types() -> tuple[str, ...]:
    return tuple(sorted(spec.artifact_type for spec in registered_specs()))


def _skill_name(artifact_type: str) -> str:
    return f"submit-{artifact_type.replace('_', '-')}-artifact"


def _format_doc_states_reference_only(artifact_type: str) -> bool:
    text = (_FORMAT_DOC_DIR / f"{artifact_type}.md").read_text(encoding="utf-8").lower()
    written_by_runtime = "runtime-generated" in text or "the runtime writes" in text
    not_submitted_by_agents = "do not submit" in text
    return written_by_runtime and not_submitted_by_agents


def _reference_only_types() -> tuple[str, ...]:
    return tuple(t for t in _artifact_types() if _format_doc_states_reference_only(t))


def _submission_types() -> tuple[str, ...]:
    reference_only = set(_reference_only_types())
    return tuple(t for t in _artifact_types() if t not in reference_only)


def _copy_content_tree(tmp_path: Path) -> Path:
    content_root = tmp_path / "content"
    shutil.copytree(_CONTENT_DIR, content_root)
    return content_root


def _correct_tree(tmp_path: Path) -> Path:
    return _copy_content_tree(tmp_path)


def _skill_text(content_root: Path, skill_name: str) -> str:
    return (content_root / f"{skill_name}.md").read_text(encoding="utf-8")


def _rewrite_skill(content_root: Path, skill_name: str, text: str) -> None:
    (content_root / f"{skill_name}.md").write_text(text, encoding="utf-8")


def _skills_pointing_at_their_format_doc(content_root: Path) -> Iterator[str]:
    for artifact_type in _submission_types():
        name = _skill_name(artifact_type)
        text = _skill_text(content_root, name)
        if f".agent/artifact-formats/{artifact_type}.md" in text:
            yield name


def test_registry_and_format_doc_type_sets_agree() -> None:
    assert set(_artifact_types()) == set(FORMAT_DOC_ARTIFACT_TYPES)


def test_exactly_one_type_is_reference_only() -> None:
    assert len(_reference_only_types()) == 1


def test_audit_reports_no_violations_for_a_correct_tree(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    assert audit_artifact_skill_types(content_root) == []


def test_missing_skill_file_is_reported(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    skill_name = _skill_name(_submission_types()[0])
    (content_root / f"{skill_name}.md").unlink()

    violations = audit_artifact_skill_types(content_root)

    assert any(f"missing skill file: {skill_name}.md" in violation for violation in violations)


def test_skill_for_a_removed_type_is_reported(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    (content_root / "submit-obsolete-type-artifact.md").write_text(
        "---\nname: submit-obsolete-type-artifact\n---\n\n# submit-obsolete-type-artifact\n",
        encoding="utf-8",
    )

    violations = audit_artifact_skill_types(content_root)

    assert any("submit-obsolete-type-artifact" in violation for violation in violations)


def test_a_stray_markdown_file_in_the_skill_bundle_is_reported(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    (content_root / "submit-obsolete-skill.md").write_text(
        "---\nname: submit-obsolete-skill\n---\n\n# submit-obsolete-skill\n",
        encoding="utf-8",
    )

    violations = audit_artifact_skill_types(content_root)

    assert any(
        "unexpected skill file: submit-obsolete-skill.md" in violation for violation in violations
    )


def test_declared_artifact_type_must_match_the_derived_type(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    artifact_type = _submission_types()[0]
    skill_name = _skill_name(artifact_type)
    other_type = _submission_types()[1]
    text = _skill_text(content_root, skill_name)
    _rewrite_skill(
        content_root,
        skill_name,
        _ARTIFACT_TYPE_RE.sub(rf'\1"{other_type}"', text),
    )

    violations = audit_artifact_skill_types(content_root)

    assert any(
        f"skill {skill_name}.md declares artifact type" in violation for violation in violations
    )


def test_skill_missing_its_artifact_type_instruction_is_reported(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    artifact_type = _submission_types()[0]
    skill_name = _skill_name(artifact_type)
    text = _skill_text(content_root, skill_name)
    _rewrite_skill(content_root, skill_name, _ARTIFACT_TYPE_RE.sub("", text))

    violations = audit_artifact_skill_types(content_root)

    assert any(
        f"submission skill {skill_name}.md is missing" in violation for violation in violations
    )


def test_reference_only_skill_with_submission_instructions_is_reported(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    skill_name = _skill_name(_reference_only_types()[0])
    text = _skill_text(content_root, skill_name)
    _rewrite_skill(
        content_root,
        skill_name,
        f"{text}\nSubmit it with `ralph_submit_md_artifact` when the run ends.\n",
    )

    violations = audit_artifact_skill_types(content_root)

    assert any(f"reference-only skill {skill_name}.md" in violation for violation in violations)


def test_reference_only_skill_gaining_the_generic_submit_template_is_reported(
    tmp_path: Path,
) -> None:
    content_root = _correct_tree(tmp_path)
    artifact_type = _reference_only_types()[0]
    skill_name = _skill_name(artifact_type)
    text = _skill_text(content_root, skill_name)
    template = (
        "\n## Submit\n\n"
        "1. Write one complete document in Markdown.\n"
        "2. Optionally preview it with `ralph_verify_md_artifact`.\n"
        f"3. Submit with `ralph_submit_md_artifact` and `artifact_type: {artifact_type}`.\n"
        "4. After a receipt, call `declare_complete` as the final action.\n"
    )
    _rewrite_skill(content_root, skill_name, f"{text}{template}")

    violations = audit_artifact_skill_types(content_root)

    assert any(f"reference-only skill {skill_name}.md" in violation for violation in violations)


def test_skill_referencing_a_missing_format_doc_is_reported(tmp_path: Path) -> None:
    content_root = _correct_tree(tmp_path)
    skill_name = next(iter(_skills_pointing_at_their_format_doc(content_root)))
    artifact_type = skill_name.removeprefix("submit-").removesuffix("-artifact").replace("-", "_")
    text = _skill_text(content_root, skill_name)
    _rewrite_skill(
        content_root,
        skill_name,
        text.replace(
            f".agent/artifact-formats/{artifact_type}.md",
            f".agent/artifact-formats/{artifact_type}-v2.md",
        ),
    )

    violations = audit_artifact_skill_types(content_root)

    assert any(
        f"skill {skill_name}.md references a missing format doc" in violation
        for violation in violations
    )


def test_main_returns_zero_for_a_correct_tree_and_one_for_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    content_root = _correct_tree(tmp_path)
    assert main([str(content_root)]) == 0

    (content_root / "submit-obsolete-type-artifact.md").write_text(
        "---\nname: submit-obsolete-type-artifact\n---\n\n# submit-obsolete-type-artifact\n",
        encoding="utf-8",
    )
    assert main([str(content_root)]) == 1
    assert "submit-obsolete-type-artifact" in capsys.readouterr().out
