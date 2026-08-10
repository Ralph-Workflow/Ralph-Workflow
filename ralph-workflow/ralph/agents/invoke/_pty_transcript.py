"""Transcript parsing helpers for PTY-based agent sessions."""

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
    """Snapshot the ``*.jsonl`` file names already present for ``project_path``.

    wt-04-claude-parsing: ``find_latest_claude_transcript_entry``'s
    ``min_mtime`` floor only proves a candidate file was *touched*
    since the floor, not that it was *created* since the floor. Every
    Claude session in the same workspace (the orchestrating session
    itself included -- it lives in the exact same ``~/.claude/projects/<key>``
    directory as any child session it spawns) shares that directory,
    so an already-active, unrelated session that keeps appending after
    the floor satisfies the ``mtime >= threshold`` filter just as well
    as the freshly-spawned session actually being tailed, and "latest
    mtime wins" can lock onto the wrong file for the entire run (the
    reader never re-resolves once ``transcript_path`` is non-``None``
    and no visible-TUI session id has been observed yet). Callers take
    this snapshot BEFORE the child process starts and pass it back in
    as ``find_latest_claude_transcript_entry(..., exclude_names=...)``
    so a pre-existing, still-active sibling session cannot masquerade
    as the new one. Returns an empty set (not an error) when the
    project directory does not exist yet -- the child's first write
    will create it, and every subsequent name is legitimately new.
    """
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
    """Return the live ``agent-<id>.jsonl`` files for ``session_id``, with their sibling ``.meta.json`` when present.

    RC1 (wt-04-claude-parsing): Claude Code writes subagent turns to a
    sibling directory (``subagents/``) rather than inline. The discovery
    helper resolves the parent ``<session-id>.jsonl`` location, then
    lists the ``subagents/agent-*.jsonl`` files under it. Each entry is
    a ``(transcript_path, meta_path_or_None)`` tuple; ``meta_path`` is
    ``None`` when the sibling ``agent-<id>.meta.json`` is missing (the
    tailer continues with best-effort correlation; see RC1 acceptance
    test 3 in ``tests/agents/invoke/test_subagent_transcript_tail.py``).

    Files are returned in mtime-ascending order (oldest first) so the
    tailer processes them in deterministic order across runs. Files
    whose mtime cannot be read are sorted to the end of the list so
    they still get tailed (the tailer does not require a parseable
    mtime; it reads the file regardless).

    The function does not raise on a missing parent directory or
    ``subagents/`` directory; an empty list is returned in those
    cases. The caller (the R7 absent-layout probe) treats an empty
    list as "layout absent" and emits the diagnostic.
    """
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
    """Return ``path``'s mtime or ``0.0`` when the stat call fails.

    A failed stat on a transient file (e.g. an in-flight write) does
    not stop the discovery; the file still gets tailed, just with an
    arbitrary sort position.
    """
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _sort_subagent_key(path: Path) -> tuple[float, str]:
    """Sort key for subagent transcripts: oldest-mtime first, name tie-breaker.

    Extracted to a named function so the lambda parameter type is
    explicit (mypy ``disallow_any_expr`` rejects the implicit ``Any``
    that ``sorted``'s ``key=`` callable would otherwise infer).
    """
    return (_safe_mtime(path), path.name)


def transcript_lines_from_events(
    raw_line: str,
    events: list[InteractiveTranscriptEvent],
) -> list[str]:
    """Convert ALREADY-PARSED transcript events for one raw line into activity lines.

    Pure function: takes the ``events`` a caller already produced via
    ``ClaudeInteractiveTranscriptParser.feed(raw_line)`` instead of
    calling ``feed`` itself. ``ClaudeInteractiveTranscriptParser`` is
    stateful (``self.session_id``, the ``_last_emitted_signature`` dedup
    cache) -- calling ``feed`` a SECOND time on the same raw line with
    the same parser instance is not idempotent: the parser has already
    advanced past that line's session id / dedup signature, so the
    second call silently returns fewer (often zero) events, and every
    line's activity is lost to the operator-facing output stream. This
    is the pure half of ``transcript_lines_from_event`` (see below) so a
    caller that ALREADY has the events (wt-04-claude-parsing:
    ``_pty_line_reader._transcript_thread`` feeds every parent record to
    the subagent tailer via one ``feed`` call) can reuse them instead of
    feeding the same line again.
    """
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
    """Convert one Claude transcript event into lossless activity lines.

    Route transcript events through the same interactive parser used elsewhere to
    decide whether the envelope carries activity. Structured activity keeps its
    original JSON envelope so tool IDs, names, inputs, and result correlation
    survive the PTY bridge. A session-only event retains the compact legacy line.

    Convenience wrapper around ``transcript_lines_from_events`` for a caller
    that has NOT already fed ``raw_line`` through ``parser`` (feeds it here,
    exactly once). A caller that already has the events from its own
    ``feed`` call MUST use ``transcript_lines_from_events`` directly instead
    of calling this function -- see that function's docstring for why a
    second ``feed`` call on the same line is not idempotent.
    """
    event_parser = parser or ClaudeInteractiveTranscriptParser()
    events = event_parser.feed(raw_line)
    return transcript_lines_from_events(raw_line, events)
