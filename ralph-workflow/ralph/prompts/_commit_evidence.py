"""Fresh, bounded evidence used by the commit-message pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.git.operations import list_changed_paths
from ralph.prompts._commit_diff import commit_generation_diff
from ralph.prompts._commit_message_budget import CommitMessageBudget

if TYPE_CHECKING:
    from pathlib import Path

_MEDIUM_CHANGE_AREAS: Final = 3
_MEDIUM_PRODUCTION_FILES: Final = 4
_DEVELOPMENT_RESULTS = (
    ".agent/artifacts/development_result.md",
    ".agent/DEVELOPMENT_RESULT.md",
)
_PARALLEL_SUMMARY = ".agent/artifacts/parallel_development_summary.md"
_VERIFICATION_RESULT = re.compile(r"(?m)^Ran:\s+yes\s+—\s+(passed|failed \(exit code (\d+)\))$")
_DIFF_PATH = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_MAX_DIFF_SUMMARY_ITEMS: Final = 6
_CHANGE_AREA_DEPTH: Final = 2


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
    diff_summary: tuple[str, ...] = ()
    fact_provenance: tuple[tuple[str, str, str, str], ...] = ()

    @property
    def message_budget(self) -> CommitMessageBudget:
        """Derive one body-item cap from all objective scope signals."""
        grounded_fact_count = sum(
            len(facts)
            for facts in (
                self.change_areas,
                self.behavior_facts,
                self.compatibility_hints,
                self.risk_hints,
                self.verification_facts,
            )
        )
        has_elevated_signal = bool(
            self.public_behavior_paths
            or self.compatibility_hints
            or self.risk_hints
            or self.verification_facts
            or self.verification_hints
        )
        if len(self.change_areas) > _MEDIUM_CHANGE_AREAS or len(self.public_behavior_paths) > _MEDIUM_PRODUCTION_FILES:
            return CommitMessageBudget("large", max(3, grounded_fact_count))
        if len(self.change_areas) > 1 or has_elevated_signal:
            return CommitMessageBudget("medium", 3)
        return CommitMessageBudget("small", 1)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _behavior_facts(repo_root: Path) -> tuple[str, ...]:
    """Read completed, durable behavior evidence; never infer claims from paths."""
    for relative_path in _DEVELOPMENT_RESULTS:
        result = _read_text(repo_root / relative_path)
        if not re.search(r"(?m)^status:\s*completed\s*$", result):
            continue
        summary = re.search(r"(?ms)^## Summary\s*\n\s*-\s+(.+?)(?=\n(?:## |\Z))", result)
        if summary is not None and (fact := " ".join(str(summary.group(1)).split())):
            return (fact,)
    return ()


def _verification_facts(repo_root: Path) -> tuple[str, ...]:
    summary = _read_text(repo_root / _PARALLEL_SUMMARY)
    match = _VERIFICATION_RESULT.search(summary)
    if match is None:
        return ()
    outcome, exit_code = str(match.group(1)), str(match.group(2))
    if outcome == "passed":
        return ("post-fanout workspace verification passed",)
    return (f"post-fanout workspace verification failed (exit code {exit_code})",)


def _change_area(path: str) -> str:
    """Use a useful, stable path area rather than a top-level bucket."""
    parts = path.split("/")
    return "/".join(parts[:_CHANGE_AREA_DEPTH]) if len(parts) > _CHANGE_AREA_DEPTH else parts[0]


def _summarize_diff(diff: str) -> tuple[str, ...]:
    """Extract bounded factual file/change summaries without inventing intent."""
    summaries: list[str] = []
    for match in _DIFF_PATH.finditer(diff):
        path = str(match.group(2))
        start = match.end()
        next_match = _DIFF_PATH.search(diff, start)
        chunk = diff[start : next_match.start() if next_match else len(diff)]
        additions = [
            " ".join(line[1:].split())
            for line in chunk.splitlines()
            if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
        ]
        detail = additions[0][:120] if additions else "modified"
        summaries.append(f"{path}: {detail}")
        if len(summaries) == _MAX_DIFF_SUMMARY_ITEMS:
            break
    return tuple(summaries)


def build_commit_evidence_bundle(repo_root: Path) -> CommitEvidenceBundle:
    """Build a new live Git snapshot; this function deliberately never caches."""
    changed_files = tuple(sorted(set(list_changed_paths(repo_root))))
    diff = commit_generation_diff(repo_root)
    areas = tuple(sorted({_change_area(path) for path in changed_files if path}))
    public_paths = tuple(path for path in changed_files if path.startswith(("ralph/", "src/", "app/", "lib/", "api/")))
    verification_hints = tuple(
        hint for hint, present in (
            ("run focused tests for changed tests", any(path.startswith("tests/") for path in changed_files)),
            ("run the verification gate", bool(public_paths)),
        ) if present
    )
    compatibility_hints = (("review public API compatibility",) if any(path.startswith(("ralph/mcp/", "api/", "src/")) for path in changed_files) else ())
    risk_hints = (("review commit staging and secret handling",) if any(path.startswith(("ralph/git/", "ralph/pipeline/", ".github/")) for path in changed_files) else ())
    verification_facts = _verification_facts(repo_root)
    behavior_facts = _behavior_facts(repo_root)
    provenance = (
        *(('behavior', fact, 'development_result', 'high') for fact in behavior_facts),
        *(('verification', fact, 'parallel_development_summary', 'high') for fact in verification_facts),
        *(('compatibility', fact, 'changed_paths', 'medium') for fact in compatibility_hints),
        *(('risk', fact, 'changed_paths', 'medium') for fact in risk_hints),
    )
    return CommitEvidenceBundle(
        diff=diff, changed_files=changed_files, change_areas=areas,
        verification_hints=verification_hints, public_behavior_paths=public_paths,
        compatibility_hints=compatibility_hints, risk_hints=risk_hints,
        verification_facts=verification_facts, behavior_facts=behavior_facts,
        diff_summary=_summarize_diff(diff), fact_provenance=provenance,
    )


__all__ = [
    "CommitEvidenceBundle",
    "CommitMessageBudget",
    "build_commit_evidence_bundle",
    "commit_generation_diff",
    "list_changed_paths",
]
