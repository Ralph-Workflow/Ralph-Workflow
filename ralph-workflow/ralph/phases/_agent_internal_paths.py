
from __future__ import annotations

import fnmatch
from pathlib import Path

AGENT_INTERNAL_TOP_LEVEL_BASENAMES: frozenset[str] = frozenset(
    {
        "PRODUCT_CRITERIA.md",
        "PLAN.md",
        "ISSUES.md",
        "DEVELOPMENT_RESULT.md",
        "FIX_RESULT.md",
        "DEVELOPMENT_ANALYSIS_DECISION.md",
        "PLANNING_ANALYSIS_DECISION.md",
        "REVIEW_ANALYSIS_DECISION.md",
        "checkpoint.json",
        "rebase_checkpoint.json",
        "rebase_checkpoint.json.bak",
        "auto_integrate_in_progress.json",
        "rebase.lock",
        "start_commit",
        "mcp.toml",
        "state.db",
        "state.db-wal",
        "state.db-shm",
    }
)

AGENT_INTERNAL_DIR_GLOBS: frozenset[str] = frozenset(
    {
        "artifacts",
        "tmp",
        "prompt_history",
        "workers",
        "receipts",
        "raw",
        "artifact-formats",
    }
)

AGENT_INTERNAL_ROOT_BASENAMES: frozenset[str] = frozenset(
    {
        "checkpoint.json",
    }
)

_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB: str = "completion_seen_*.json"

_AGENT_INTERNAL_DIR_FILE_EXTENSIONS: dict[str, frozenset[str]] = {
    "raw": frozenset({".log"}),
    "tmp": frozenset({".log", ".md", ".json"}),
    "artifacts": frozenset({".md"}),
    "receipts": frozenset({".json"}),
    "prompt_history": frozenset({".json"}),
    "artifact-formats": frozenset({".md"}),
    "workers": frozenset({".log", ".md", ".json"}),
}

_SEGMENT_COUNT_ONE: int = 1
_SEGMENT_COUNT_TWO: int = 2


def is_agent_internal_path(path: str) -> bool:
    if not path:
        return False

    candidate = Path(path)
    parts = candidate.parts
    name = candidate.name

    if len(parts) == _SEGMENT_COUNT_ONE:
        return name in AGENT_INTERNAL_ROOT_BASENAMES

    if parts[0] != ".agent":
        return False

    second = parts[1]
    two_segment_match = len(parts) == _SEGMENT_COUNT_TWO and (
        second in AGENT_INTERNAL_TOP_LEVEL_BASENAMES
        or fnmatch.fnmatchcase(name, _AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB)
    )
    if two_segment_match:
        return True

    return _is_engine_file_in_dir(candidate, second)


def _is_engine_file_in_dir(candidate: Path, dir_segment: str) -> bool:
    if dir_segment not in AGENT_INTERNAL_DIR_GLOBS:
        return False
    allowed_extensions = _AGENT_INTERNAL_DIR_FILE_EXTENSIONS.get(dir_segment)
    if allowed_extensions is None:
        return False
    return candidate.suffix.lower() in allowed_extensions
