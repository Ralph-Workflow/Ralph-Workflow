from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

from ralph.mcp.artifacts.format_docs import FORMAT_DOC_ARTIFACT_TYPES

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PACKAGE_ROOT / "ralph" / "skills"

RETIRED_BUNDLE_SIZE = 29
RETIRED_MIRRORED_ONLY_SKILLS = frozenset(
    {
        "brainstorming",
        "coding-standards",
        "dispatching-parallel-agents",
        "executing-plans",
        "finishing-a-development-branch",
        "open-design--baseline-ui",
        "open-design--fixing-accessibility",
        "open-design--fixing-metadata",
        "open-design--fixing-motion-performance",
        "open-design--frontend-design",
        "open-design--frontend-slides",
        "open-design--theme-factory",
        "receiving-code-review",
        "requesting-code-review",
        "security-review",
        "using-git-worktrees",
        "using-superpowers",
        "verification-loop",
        "writing-plans",
        "writing-skills",
    }
)

SCHEDULER_FILE_SUFFIXES = frozenset({".cron", ".crontab", ".plist", ".service", ".timer"})
SCHEDULER_SOURCE_PATTERN = re.compile(
    r"\b(?:APScheduler|apscheduler|croniter|crontab|launchd|schtasks|systemctl)\b"
    r"|\bschedule\s*\.\s*(?:every|repeat|run_pending)\b"
)


def _packaged_artifact_skill_names() -> frozenset[str]:
    content = files("ralph.skills").joinpath("content")
    return frozenset(
        entry.name.removesuffix(".md")
        for entry in content.iterdir()
        if entry.is_file() and entry.name.endswith(".md")
    )


def _expected_artifact_skill_names() -> frozenset[str]:
    typed = {
        f"submit-{artifact_type.replace('_', '-')}-artifact"
        for artifact_type in FORMAT_DOC_ARTIFACT_TYPES
    }
    return frozenset({"submit-artifact", *typed})


def test_packaged_skill_inventory_is_smaller_than_the_retired_bundle() -> None:
    packaged = _packaged_artifact_skill_names()
    expected = _expected_artifact_skill_names()

    assert packaged == expected, (
        "the packaged defaults must contain only the umbrella and artifact-type-derived skills"
    )
    assert len(packaged) < RETIRED_BUNDLE_SIZE, (
        "the packaged default inventory must remain smaller than the retired 29-skill bundle"
    )
    assert packaged.isdisjoint(RETIRED_MIRRORED_ONLY_SKILLS), (
        "skills removed with the mirrored bundle must not return to packaged defaults"
    )


def test_skills_use_no_os_scheduler_registration(tmp_path: Path) -> None:
    scheduler_artifacts = sorted(
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in SKILLS_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in SCHEDULER_FILE_SUFFIXES
    )
    scheduler_references = sorted(
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in SKILLS_ROOT.rglob("*.py")
        if SCHEDULER_SOURCE_PATTERN.search(path.read_text(encoding="utf-8"))
    )

    assert scheduler_artifacts == [], (
        "the skills subsystem must not ship cron, systemd, launchd, or timer registrations"
    )
    assert scheduler_references == [], (
        "skill updates must use the in-process timestamp cadence, not a scheduler library or OS job"
    )
    assert not any(tmp_path.iterdir()), "the repository-shape check must not create scheduler state"


def test_no_replacement_skill_manifest_or_sync_script_is_shipped() -> None:
    forbidden_paths = (
        PACKAGE_ROOT / "skills-package",
        PACKAGE_ROOT / "bin" / "skills.js",
        PACKAGE_ROOT / "bin" / "sync-upstream-skills.js",
        SKILLS_ROOT / "manifest.json",
        SKILLS_ROOT / "upstream-skills.json",
        SKILLS_ROOT / "package.json",
    )
    standalone_sync_scripts = sorted(
        path.relative_to(PACKAGE_ROOT).as_posix()
        for root in (PACKAGE_ROOT / "bin", PACKAGE_ROOT / "scripts")
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
        and "skill" in path.name.lower()
        and any(term in path.name.lower() for term in ("sync", "update"))
    )

    assert not any(path.exists() for path in forbidden_paths), (
        "the retired skills package and its manifest or executable entry points must stay absent"
    )
    assert standalone_sync_scripts == [], (
        "skill updates must remain in the orchestrator rather than a standalone sync script"
    )
