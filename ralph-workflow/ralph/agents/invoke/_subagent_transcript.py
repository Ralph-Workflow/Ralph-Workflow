"""Subagent transcript tailer (RC1 + RC3 of wt-04-claude-parsing).

Claude Code >= 2.1.221 writes subagent (sidechain) turns to a sibling
directory rather than inline:

  ~/.claude/projects/<project-key>/<session-id>.jsonl
  ~/.claude/projects/<project-key>/<session-id>/subagents/agent-<agentId>.jsonl
  ~/.claude/projects/<project-key>/<session-id>/subagents/agent-<agentId>.meta.json

The ``.meta.json`` carries ``{agentType, description, toolUseId, spawnDepth}``
so each child transcript can be correlated back to the parent's
``tool_use`` block.

Before this module, the PTY line reader's ``_transcript_thread``
(``ralph/agents/invoke/_pty_line_reader.py:611``) tailed exactly one
file: the parent ``<session-id>.jsonl``. Subagent work was invisible
to Ralph regardless of how much the agent produced, which is the
RC1 root cause of the kill/resume loop that motivated this task.

This module provides :class:`ClaudeSubagentTranscriptTails`, the
file-system discovery + tail loop. The PTY line reader mounts one
``ClaudeSubagentTranscriptTails`` per invocation, on the same
``start()`` event as the parent tail, and stops it on
``_monitor_stop``. Subagent events do NOT enter the parent foreground
output queue; they enter the operator-visible channel via a
**dedicated** ``SUBAGENT_PROGRESS`` path so R3 holds (parent turns
and subagent turns are attributed, not flattened). The watchdog's
``record_subagent_work`` channel stays fresh (RC3 / R2); the idle
baseline is reset on every observed subagent event.

Tail lifecycle ownership:

  A discovered child file is polled continuously from discovery
  through whichever comes first:

  (a) the parent transcript emits the matching ``tool_result`` block
      for that child (the parent's ``tool_use_id`` is correlated with
      the child's ``toolUseId`` via the ``toolUseId`` parsed from
      ``agent-*.meta.json``), or
  (b) the tailer shuts down (``_monitor_stop``).

  Stale ``mtime`` is **never** a stop condition: a quiet in-process
  child can have a stale mtime yet emit a fresh transcript line one
  tick later, and that event MUST reach the watchdog. File mtime is
  read only as a visibility hint for selecting which file to advance
  next in the poll loop; it has no semantic authority. The watchdog's
  ``record_subagent_work`` channel and the freshness classifier in
  ``_activity_methods.py`` / ``_stuck_classifier.py`` are the single
  authority on whether a quiet child should fire the watchdog.

R7 absent-layout probe (dispatch-driven):

  When the parent parser yields a ``tool_use`` block whose ``name`` is
  in the subagent-dispatch set ``{"Agent", "Task"}`` (only ``Agent``
  is emitted on Claude Code 2.1.223; ``Task`` is the historical alias
  and MUST be accepted too), the tailer:

  (i)   captures the dispatch's ``tool_use_id`` and the parent session's
        recorded Claude Code ``version`` (top-level field on every
        user/assistant record);
  (ii)  probes the directory ``<project-key>/<session-id>/subagents/``
        at the moment of dispatch;
  (iii) if the directory is missing OR exists but contains zero
        ``agent-*.jsonl`` entries, emits exactly one structured R7
        diagnostic carrying ``code="R7_SUBAGENT_LAYOUT_MISSING"``,
        ``claude_code_version=<version>``, ``project_key=<project_key>``,
        ``session_id=<session_id>``, ``probed_path=<absolute path>``,
        ``dispatch_tool_use_id=<id>``, ``dispatch_tool_name=<Agent|Task>``;
  (iv)  the probe runs once per dispatched ``tool_use_id``;
  (v)   if the directory appears later (e.g. created mid-session),
        the discovery poll loop picks it up normally;
  (vi)  R7 fires whether or not the parent produces any descendant
        activity.
  R7 is NOT triggered by any other shape -- a missing ``subagents/``
  with no ``Agent`` / ``Task`` dispatch produces zero diagnostics.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._pty_transcript import (
    find_claude_subagent_transcripts,
)
from ralph.agents.invoke._r7_diagnostic import R7AbsentLayoutDiagnostic
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from io import TextIOBase

    from ralph.agents.parsers.interactive_transcript_event import (
        InteractiveTranscriptEvent,
    )

# Subagent-dispatch set: the canonical tool name Claude Code 2.1.x emits
# is ``Agent``; ``Task`` is the historical alias and MUST be accepted too
# so a future build that switches back to ``Task`` does not silently
# disable the R7 contract. Verified live: Claude Code 2.1.223 emits only
# ``Agent``; older builds used ``Task``.
SUBAGENT_DISPATCH_TOOLS: frozenset[str] = frozenset({"Agent", "Task"})

#: Truncation length for text / thinking summaries fed to the
#: subagent sink. Matches the canonical parser-hook limit
#: (``_MAX_PREFIX_LENGTH``) so the surfaced summary is consistent
#: across the parent and child paths.
SUMMARY_TRUNCATE_LENGTH: int = 80


def read_meta_file(meta_path: Path) -> dict[str, object] | None:
    """Parse the sibling ``agent-<id>.meta.json`` for a child transcript.

    The ``.meta.json`` is small (a few hundred bytes) and is read
    exactly once per file at discovery time. A missing or
    unparseable file returns ``None`` so the caller can fall back to
    best-effort correlation. The function never raises; it returns
    ``None`` for any I/O or JSON-decode error.
    """
    if not meta_path.is_file():
        return None
    try:
        with meta_path.open("r", encoding="utf-8") as handle:
            data: object = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


class ClaudeSubagentTranscriptTails:
    """Discover and tail ``subagents/agent-*.jsonl`` files for one parent session.

    Construction is cheap: no I/O until :meth:`start` is called. The
    tailer is wired with the captured session id, the project-key,
    the monitor stop event, the watchdog subagent sink, the R7 sink,
    the parent tool_use_id correlation registry, and the parent's
    discovered Claude Code version (from the first parent user /
    assistant record). All threads join on :meth:`stop` and
    :meth:`wait` so a clean shutdown is deterministic.
    """

    def __init__(
        self,
        *,
        session_id: str,
        project_key: str,
        monitor_stop: threading.Event,
        subagent_sink: Callable[[str], None],
        r7_sink: Callable[[R7AbsentLayoutDiagnostic], None],
        poll_interval_seconds: float = 0.1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session_id = session_id
        self._project_key = project_key
        self._monitor_stop = monitor_stop
        self._subagent_sink = subagent_sink
        self._r7_sink = r7_sink
        self._poll_interval = poll_interval_seconds
        self._clock: Callable[[], float] = clock or _default_clock
        # Per-child tail state. Each entry is
        # ``(transcript_path, meta_path_or_None, meta_dict_or_None,
        #     file_handle, parser, byte_offset)``. The dict is mutated
        # in-place as new files appear and old files complete; ``stop``
        # closes every open handle and clears the dict.
        self._tails: dict[str, tuple[
            Path,
            Path | None,
            dict[str, object] | None,
            TextIOBase,
            ClaudeInteractiveTranscriptParser,
            int,
        ]] = {}  # bounded-accumulator-ok: drained in stop(); bounded by dispatch fan-out
        # Per-dispatch probe registry so R7 fires once per
        # ``tool_use_id``. The key is ``dispatch_tool_use_id``.
        self._probed_dispatch_ids: set[str] = set()  # bounded-accumulator-ok: bounded by dispatch fan-out
        # ``tool_use_id`` values the parent has marked completed via
        # ``note_completion``. A discovered child whose ``toolUseId``
        # is in this set is dropped immediately (the parent's
        # ``tool_result`` already landed before the child file was
        # discovered, which is the common case when the child wrote
        # its transcript AFTER the parent's ``tool_result`` for the
        # fast-returning subagent dispatch).
        self._completed_dispatch_ids: set[str] = set()  # bounded-accumulator-ok: bounded by dispatch fan-out
        # Parent Claude Code version captured from the first user /
        # assistant record. ``None`` until observed.
        self._claude_code_version: str | None = None
        self._thread: threading.Thread | None = None
        # Bookkeeping for stop() ordering: ``stop`` is idempotent and
        # safe to call from any thread.
        self._stopped = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def probed_dispatch_ids(self) -> frozenset[str]:
        """The set of dispatch tool_use_ids the R7 probe has already fired on."""
        return frozenset(self._probed_dispatch_ids)

    def note_parent_record(self, obj: dict[str, object]) -> None:
        """Capture the parent Claude Code ``version`` from a user/assistant record.

        The version field is the top-level ``obj.version``; the
        parent parser surfaces every user/assistant record via the
        transcript event stream, and the tailer is wired into the
        same feed. The version is captured exactly once (the first
        observation wins) so a stale parent record cannot overwrite
        a newer build's version.
        """
        if self._claude_code_version is not None:
            return
        version: object = obj.get("version")
        if isinstance(version, str) and version:
            self._claude_code_version = version

    def note_completion(self, *, tool_use_id: str) -> bool:
        """Drop a child file whose ``toolUseId`` matches the parent's ``tool_result.tool_use_id``.

        Lifecycle ownership: a discovered child file is polled
        continuously from discovery through whichever comes first:

        (a) the parent transcript emits the matching ``tool_result``
            block for that child (the parent's ``tool_use_id`` is
            correlated with the child's ``toolUseId`` via the
            ``toolUseId`` parsed from ``agent-*.meta.json``), or
        (b) the tailer shuts down (``_monitor_stop``).

        The ``tool_use_id`` is recorded in
        ``_completed_dispatch_ids`` so a child file that appears
        AFTER the parent ``tool_result`` lands (the common case
        when the child wrote its transcript after the parent
        already returned) is dropped on its first discovery tick.

        Returns ``True`` when a matching child file was found and
        dropped; ``False`` otherwise. The return value is exposed
        for the test surface so a regression can assert the
        lifecycle boundary fires on the right ``tool_result``. The
        dropped child file's file handle is closed deterministically
        and the parser instance released; future reads against the
        same child file are not attempted.
        """
        if not tool_use_id:
            return False
        self._completed_dispatch_ids.add(tool_use_id)
        return self._drop_child_for_tool_use_id(tool_use_id)

    def _drop_child_for_tool_use_id(self, tool_use_id: str) -> bool:
        """Internal helper: scan ``_tails`` for a child whose ``toolUseId`` matches.

        Used by both ``note_completion`` (eager drop) and
        ``_discover_new_files`` (drop on first observation if the
        parent's ``tool_result`` already landed).
        """
        for key in list(self._tails.keys()):
            entry = self._tails[key]
            _transcript_path, _meta_path, meta_dict, file_obj, _parser, _offset = entry
            child_use_id: object = (
                meta_dict.get("toolUseId") if isinstance(meta_dict, dict) else None
            )
            if not isinstance(child_use_id, str) or child_use_id != tool_use_id:
                continue
            with contextlib.suppress(Exception):
                file_obj.close()
            del self._tails[key]
            return True
        return False

    @property
    def is_started(self) -> bool:
        """Return whether the tail thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def claude_code_version(self) -> str | None:
        """Return the captured parent Claude Code version (``None`` until observed)."""
        return self._claude_code_version

    def note_dispatch(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
    ) -> None:
        """Record a subagent dispatch and probe the ``subagents/`` layout for R7.

        Called by the parent's transcript parser when a ``tool_use``
        block matches ``SUBAGENT_DISPATCH_TOOLS``. The probe runs
        synchronously (cheap directory enumeration); the R7 diagnostic
        is emitted via ``r7_sink`` if the layout is absent.

        Once-per-dispatch dedup: a second dispatch with the same
        ``tool_use_id`` re-probes (R7 is meant to surface future
        renames, not dedupe dispatches).
        """
        if tool_name not in SUBAGENT_DISPATCH_TOOLS:
            return
        if tool_use_id in self._probed_dispatch_ids:
            return
        self._probed_dispatch_ids.add(tool_use_id)
        # Probe the canonical path directly so we don't depend on
        # the poll loop's view of ``self._tails``. The tailer may
        # not have started polling yet (the parent transcript
        # thread can observe a dispatch before the tailer thread
        # has run its first discovery tick), so the only
        # reliable "is the layout present?" check is the canonical
        # ``<project>/<session>/subagents/`` directory enumeration.
        probe_path = (
            Path.home()
            / ".claude"
            / "projects"
            / self._project_key
            / self._session_id
            / "subagents"
        )
        if not probe_path.is_dir():
            self._emit_r7(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                probed_path=probe_path,
            )
            return
        try:
            contents = list(probe_path.glob("agent-*.jsonl"))
        except OSError:
            self._emit_r7(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                probed_path=probe_path,
            )
            return
        if not contents:
            self._emit_r7(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                probed_path=probe_path,
            )
            return

    def start(self) -> threading.Thread:
        """Start the tail thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(
            target=self._run,
            name=f"subagent-transcript-tail[{self._session_id}]",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def stop(self, *, join_timeout_seconds: float = 2.0) -> None:
        """Stop the tail thread and close every open file handle.

        Idempotent: a second call is a no-op. Joins the thread with a
        bounded timeout so a stuck read cannot wedge the PTY line
        reader's ``_cleanup``.
        """
        if self._stopped:
            return
        self._stopped = True
        self._monitor_stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_seconds)
        for _key, (transcript_path, _meta, _meta_dict, file_obj, _parser, _offset) in list(
            self._tails.items()
        ):
            with contextlib.suppress(Exception):
                file_obj.close()
            del transcript_path
        self._tails.clear()

    def wait(self, *, timeout_seconds: float | None = None) -> None:
        """Block until the tail thread exits or the timeout elapses."""
        if self._thread is None:
            return
        self._thread.join(timeout=timeout_seconds)

    def _run(self) -> None:
        """Tail-loop body: discover, advance, and forward events."""
        try:
            while not self._monitor_stop.is_set():
                self._discover_new_files()
                self._advance_all_tails()
                if self._monitor_stop.wait(self._poll_interval):
                    break
        except Exception:
            # The tail loop must NEVER crash the parent PTY reader.
            # The watchdog's evidence channel already records the
            # parent as a live subagent (``subagent_progress_count``);
            # a tailer crash simply means we stop forwarding new
            # events. The watchdog's own evaluator picks up the
            # absence via ``alive_by is None`` and the
            # ``SILENT_SUBAGENT`` diagnostic surfaces the gap.
            return

    def _discover_new_files(self) -> None:
        """Add any newly-discovered child files to the tail set.

        A discovered child whose ``toolUseId`` is already in
        ``_completed_dispatch_ids`` (the parent's ``tool_result``
        landed before the child file appeared on disk) is NOT
        added to ``_tails``; the parent's lifecycle boundary
        wins. The dropped-on-discovery case is the common
        fast-returning-child pattern and the test surface asserts
        it explicitly.
        """
        for transcript_path, meta_path in find_claude_subagent_transcripts(
            self._session_id
        ):
            key = str(transcript_path)
            if key in self._tails:
                continue
            meta_dict = read_meta_file(meta_path) if meta_path is not None else None
            if isinstance(meta_dict, dict):
                child_use_id: object = meta_dict.get("toolUseId")
                if (
                    isinstance(child_use_id, str)
                    and child_use_id in self._completed_dispatch_ids
                ):
                    # The parent's ``tool_result`` already landed
                    # for this child. Drop on first observation;
                    # do not register a tail entry, do not open a
                    # file handle, do not start a parser.
                    continue
            parser = ClaudeInteractiveTranscriptParser()
            try:
                file_obj = transcript_path.open("r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            self._tails[key] = (transcript_path, meta_path, meta_dict, file_obj, parser, 0)

    def _advance_all_tails(self) -> None:
        """Read any new bytes from each tailed file and forward events."""
        for key in list(self._tails.keys()):
            entry = self._tails[key]
            transcript_path, _meta_path, meta_dict, file_obj, parser, offset = entry
            try:
                # Stat the file to detect truncation/rotation. The
                # tailer does not abort on truncation (it simply
                # rewinds to byte 0 the next tick) but it does NOT
                # use mtime as a stop condition -- a quiet in-process
                # child can have a stale mtime and still emit a fresh
                # line on the next tick.
                file_obj.seek(0, 2)  # seek to end to learn the new size
                end = file_obj.tell()
                if end < offset:
                    # File was truncated/rotated; rewind to byte 0.
                    file_obj.seek(0)
                    offset = 0
                if end > offset:
                    file_obj.seek(offset)
                    chunk = file_obj.read(end - offset)
                    offset = end
                    self._forward_chunk(parser, chunk, transcript_path, meta_dict)
            except OSError:
                continue
            self._tails[key] = (transcript_path, _meta_path, meta_dict, file_obj, parser, offset)

    def _forward_chunk(
        self,
        parser: ClaudeInteractiveTranscriptParser,
        chunk: str,
        transcript_path: Path,
        meta_dict: dict[str, object] | None,
    ) -> None:
        """Feed a new chunk through the parser and forward events to the watchdog sink.

        The sink accepts a ``tool_use:<name>`` / ``tool_result:<name>`` /
        ``text:<first-80>`` / ``thinking:<first-80>`` summary; the parser
        produces ``InteractiveTranscriptEvent`` records that the caller
        converts. To stay agnostic of the parser's event vocabulary
        (the new subagent tailer is wired in BEFORE the parser wrapping
        lands), the tailer forwards each event's text verbatim through
        the subagent sink with a synthetic ``tool_use:Subagent`` /
        ``tool_result:Subagent`` prefix; the existing parser hook in
        ``ClaudeInteractiveParser.emit_subagent_activity`` (line 71)
        formats the canonical summary for the watchdog's
        ``record_subagent_work`` channel.

        Sink exceptions are swallowed so a buggy sink cannot crash the
        tailer; this is the same defensive pattern as the parent parser's
        hook.
        """
        for raw_line in chunk.splitlines():
            try:
                events = parser.feed(raw_line)
            except Exception:
                continue
            for event in events:
                if event.kind == "session":
                    continue
                summary = _summarize_event(event, meta_dict, transcript_path)
                if not summary:
                    continue
                try:
                    self._subagent_sink(summary)
                except Exception:
                    continue

    def _emit_r7(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
        probed_path: Path,
    ) -> None:
        """Emit one R7 diagnostic via ``r7_sink``.

        Sink exceptions are swallowed so a misbehaving sink cannot
        crash the tail loop.
        """
        diag = R7AbsentLayoutDiagnostic(
            code="R7_SUBAGENT_LAYOUT_MISSING",
            claude_code_version=self._claude_code_version,
            project_key=self._project_key,
            session_id=self._session_id,
            probed_path=str(probed_path.resolve()),
            dispatch_tool_use_id=tool_use_id,
            dispatch_tool_name=tool_name,
        )
        try:
            self._r7_sink(diag)
        except Exception:
            return


def _default_clock() -> float:
    """Wall-clock fallback used when no clock is injected (production paths)."""
    return time.monotonic()


def _safe_metadata_get(metadata: object, key: str, default: object) -> object:
    """Look up ``key`` on a possibly-typed mapping without leaking ``Any``.

    ``InteractiveTranscriptEvent.metadata`` is typed as ``dict[str, object]``
    in the production parser, but the type is loose here so the helper
    works for any object with a ``.get`` method. The wrapper hides the
    ``Any | None`` mypy noise from the call site.
    """
    if not hasattr(metadata, "get"):
        return default
    result: object = metadata.get(key, default)
    return result


def _summarize_tool_kind(event: object, prefix: str) -> str | None:
    """Render ``tool_use`` / ``tool_result`` summaries with the shared prefix logic."""
    metadata: object = getattr(event, "metadata", None)
    tool_obj: object = _safe_metadata_get(metadata, "tool", "Subagent")
    tool_name = str(tool_obj)
    return f"{prefix}:{tool_name}"


def _summarize_text_kind(event: object, prefix: str) -> str | None:
    """Render ``text`` / ``thinking`` summaries, truncating to the first 80 chars."""
    text_raw: object = getattr(event, "text", "")
    text = str(text_raw).strip()
    if not text:
        return None
    if len(text) > SUMMARY_TRUNCATE_LENGTH:
        text = text[:SUMMARY_TRUNCATE_LENGTH]
    return f"{prefix}:{text}"


def summarize_event(
    event: InteractiveTranscriptEvent,
    meta_dict: dict[str, object] | None,
    transcript_path: Path,
) -> str | None:
    """Convert an ``InteractiveTranscriptEvent`` into a subagent-sink summary.

    Public surface (no leading underscore) so tests can import it
    without crossing the ``_subagent_transcript`` private-module
    boundary that the audit_repo_structure policy enforces.

    Mirrors the canonical parser-hook format
    (``ClaudeInteractiveParser.emit_subagent_activity``): tool calls
    use ``tool_use:<name>``, tool results use
    ``tool_result:<name>``, text uses ``text:<first-80>``, thinking uses
    ``thinking:<first-80>``. The ``Subagent`` tool-name placeholder
    distinguishes child turns from parent turns in the watchdog's
    diagnostic snapshot.
    """
    kind: object = getattr(event, "kind", None)
    if kind == "tool_use":
        return _summarize_tool_kind(event, "tool_use")
    if kind == "tool_result":
        return _summarize_tool_kind(event, "tool_result")
    if kind == "text":
        return _summarize_text_kind(event, "text")
    if kind == "thinking":
        return _summarize_text_kind(event, "thinking")
    return None


# Private alias kept for any in-module caller that previously used the
# underscored name. The public surface is ``summarize_event`` (above).
_summarize_event = summarize_event


__all__ = [
    "SUBAGENT_DISPATCH_TOOLS",
    "ClaudeSubagentTranscriptTails",
    "R7AbsentLayoutDiagnostic",
    "read_meta_file",
    "summarize_event",
]
