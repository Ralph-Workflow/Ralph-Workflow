from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)

if TYPE_CHECKING:
    from ralph.agents.parsers.interactive_transcript_event import InteractiveTranscriptEvent


def _session_id_candidates(value: str | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(candidate for candidate in value if candidate)


def _path_name(path: Path) -> str:
    return path.name


def _project_transcript_root(project_path: Path) -> Path:
    project_key = str(project_path.resolve()).replace("/", "-").replace(" ", "-")
    return Path.home() / ".claude" / "projects" / project_key


def _path_mtime(path: Path) -> float:
    return path.stat().st_mtime


def find_claude_transcript_entry(
    session_id: str | tuple[str, ...],
) -> tuple[Path, str] | None:
    candidates = _session_id_candidates(session_id)
    if not candidates:
        return None
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.exists():
        return None
    for candidate_root in sorted(projects_root.iterdir(), key=_path_name):
        for candidate_session_id in candidates:
            candidate = candidate_root / f"{candidate_session_id}.jsonl"
            if candidate.is_file():
                return candidate, candidate_session_id
    return None


def existing_transcript_names(project_path: Path) -> frozenset[str]:
    project_root = _project_transcript_root(project_path)
    if not project_root.exists():
        return frozenset()
    return frozenset(candidate.name for candidate in project_root.glob("*.jsonl"))


def find_latest_claude_transcript_entry(
    project_path: Path,
    *,
    min_mtime: float | None = None,
    exclude_names: frozenset[str] | None = None,
) -> tuple[Path, str] | None:
    project_root = _project_transcript_root(project_path)
    if not project_root.exists():
        return None
    threshold = time.time() - 5.0 if min_mtime is None else min_mtime
    candidates: list[Path] = [
        candidate
        for candidate in project_root.glob("*.jsonl")
        if candidate.stat().st_mtime >= threshold
        and (exclude_names is None or candidate.name not in exclude_names)
    ]
    candidates.sort(key=_path_mtime, reverse=True)
    if not candidates:
        return None
    latest = candidates[0]
    return latest, latest.stem


def find_claude_transcript_path(session_id: str) -> Path | None:
    entry = find_claude_transcript_entry(session_id)
    return entry[0] if entry is not None else None


def find_claude_subagent_transcripts(session_id: str) -> list[tuple[Path, Path | None]]:
    parent_path = find_claude_transcript_path(session_id)
    if parent_path is None:
        return []
    subagents_dir = parent_path.parent / session_id / "subagents"
    if not subagents_dir.is_dir():
        return []
    files: list[Path] = sorted(
        subagents_dir.glob("agent-*.jsonl"),
        key=_sort_subagent_key,
    )
    result: list[tuple[Path, Path | None]] = []
    for transcript_path in files:
        meta_path = transcript_path.with_suffix(".meta.json")
        result.append((transcript_path, meta_path if meta_path.is_file() else None))
    return result


def _safe_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _sort_subagent_key(path: Path) -> tuple[float, str]:
    return (_safe_mtime(path), path.name)


def transcript_lines_from_events(
    raw_line: str,
    events: list[InteractiveTranscriptEvent],
) -> list[str]:
    if any(event.kind != "session" for event in events):
        return [raw_line if raw_line.endswith("\n") else f"{raw_line}\n"]
    for event in events:
        text = event.text.strip()
        if event.kind == "session" and text:
            return [f"Session ID: {text}\n"]
    return []


def transcript_lines_from_event(
    raw_line: str,
    parser: ClaudeInteractiveTranscriptParser | None = None,
) -> list[str]:
    event_parser = parser or ClaudeInteractiveTranscriptParser()
    events = event_parser.feed(raw_line)
    return transcript_lines_from_events(raw_line, events)
