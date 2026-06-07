"""Transcript parsing helpers for PTY-based agent sessions."""

from __future__ import annotations

from pathlib import Path

from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)


def _session_id_candidates(value: str | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(candidate for candidate in value if candidate)


def _path_name(path: Path) -> str:
    return path.name


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


def find_claude_transcript_path(session_id: str) -> Path | None:
    entry = find_claude_transcript_entry(session_id)
    return entry[0] if entry is not None else None


def transcript_lines_from_event(
    raw_line: str,
    parser: ClaudeInteractiveTranscriptParser | None = None,
) -> list[str]:
    """Convert one Claude transcript event into semantic PTY lines.

    Route transcript events through the same interactive parser used elsewhere so
    the PTY bridge stays aligned with Claude event semantics instead of drifting.
    """

    event_parser = parser or ClaudeInteractiveTranscriptParser()
    lines: list[str] = []
    for event in event_parser.feed(raw_line):
        if event.kind == "session":
            continue
        text = event.text.strip()
        if text:
            lines.append(f"{text}\n")
    return lines
