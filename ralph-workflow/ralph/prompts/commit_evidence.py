"""Fresh, factual evidence used by the commit-message pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.git.operations import list_changed_paths
from ralph.prompts._commit_diff import commit_generation_diff

if TYPE_CHECKING:
    from pathlib import Path

_MEDIUM_CHANGE_AREAS: Final = 3
_MEDIUM_PRODUCTION_FILES: Final = 4
_DEVELOPMENT_RESULTS = (
    ".agent/artifacts/development_result.md",
    ".agent/DEVELOPMENT_RESULT.md",
)
_PARALLEL_SUMMARY = ".agent/artifacts/parallel_development_summary.md"
_VERIFICATION_RESULT = re.compile(
    r"(?m)^Ran:\s+yes\s+—\s+(passed|failed \(exit code (\d+)\))$"
)


@dataclass(frozen=True)
class CommitEvidenceBundle:
    """A live snapshot of the work a commit may describe."""

    diff: str
    changed_files: tuple[str, ...]
    change_areas: tuple[str, ...]
    verification_hints: tuple[str, ...]
    public_behavior_paths: tuple[str, ...]
    compatibility_hints: tuple[str, ...] = ()
    risk_hints: tuple[str, ...] = ()
    verification_facts: tuple[str, ...] = ()
    behavior_facts: tuple[str, ...] = ()

    @property
    def message_budget(self) -> str:
        """Return the evidence-derived detail target for message authors."""
        areas = len(self.change_areas)
        production = len(self.public_behavior_paths)
        if areas <= 1 and production <= 1:
            return "small: one focused body point"
        if areas <= _MEDIUM_CHANGE_AREAS and production <= _MEDIUM_PRODUCTION_FILES:
            return "medium: two or three focused body points"
        return "large: cover each material area, risks, and verification"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _behavior_facts(repo_root: Path) -> tuple[str, ...]:
    """Read the completed development summary, never infer behavior from paths."""
    for relative_path in _DEVELOPMENT_RESULTS:
        result = _read_text(repo_root / relative_path)
        if not re.search(r"(?m)^status:\s*completed\s*$", result):
            continue
        summary = re.search(r"(?ms)^## Summary\s*\n\s*-\s+(.+?)(?=\n(?:## |\Z))", result)
        if summary is None:
            continue
        fact = " ".join(str(summary.group(1)).split())
        if fact:
            return (fact,)
    return ()


def _verification_facts(repo_root: Path) -> tuple[str, ...]:
    """Read only a persisted verification outcome; suggestions are not facts."""
    summary = _read_text(repo_root / _PARALLEL_SUMMARY)
    match = _VERIFICATION_RESULT.search(summary)
    if match is None:
        return ()
    outcome = str(match.group(1))
    exit_code = str(match.group(2))
    if outcome == "passed":
        return ("post-fanout workspace verification passed",)
    return (f"post-fanout workspace verification failed (exit code {exit_code})",)


def build_commit_evidence_bundle(repo_root: Path) -> CommitEvidenceBundle:
    """Build a new evidence snapshot from live Git state; never cache it."""
    changed_files = tuple(sorted(set(list_changed_paths(repo_root))))
    areas = tuple(sorted({path.split("/", 1)[0] for path in changed_files if path}))
    public_paths = tuple(
        path for path in changed_files if path.startswith(("ralph/", "src/", "app/", "lib/", "api/"))
    )
    verification_hints = tuple(
        hint
        for hint, present in (
            ("run focused tests for changed tests", any(path.startswith("tests/") for path in changed_files)),
            ("run the verification gate", bool(public_paths)),
        )
        if present
    )
    compatibility_hints = (
        ("review public API compatibility",) if any(path.startswith(("ralph/mcp/", "api/", "src/")) for path in changed_files) else ()
    )
    risk_hints = (
        ("review commit staging and secret handling",)
        if any(path.startswith(("ralph/git/", "ralph/pipeline/", ".github/")) for path in changed_files)
        else ()
    )
    return CommitEvidenceBundle(
        diff=commit_generation_diff(repo_root),
        changed_files=changed_files,
        change_areas=areas,
        verification_hints=verification_hints,
        public_behavior_paths=public_paths,
        compatibility_hints=compatibility_hints,
        risk_hints=risk_hints,
        verification_facts=_verification_facts(repo_root),
        behavior_facts=_behavior_facts(repo_root),
    )


__all__ = ["CommitEvidenceBundle", "build_commit_evidence_bundle"]
