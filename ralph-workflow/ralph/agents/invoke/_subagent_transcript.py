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

SUBAGENT_DISPATCH_TOOLS: frozenset[str] = frozenset({"Agent", "Task"})

SUMMARY_TRUNCATE_LENGTH: int = 80


def read_meta_file(meta_path: Path) -> dict[str, object] | None:
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
        self._tails: dict[
            str,
            tuple[
                Path,
                Path | None,
                dict[str, object] | None,
                TextIOBase,
                ClaudeInteractiveTranscriptParser,
                int,
            ],
        ] = {}
        self._tails_lock = threading.RLock()
        self._probed_dispatch_ids: set[str] = set()
        self._completed_dispatch_ids: set[str] = set()
        self._claude_code_version: str | None = None
        self._thread: threading.Thread | None = None
        self._stopped = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def probed_dispatch_ids(self) -> frozenset[str]:
        return frozenset(self._probed_dispatch_ids)

    def note_parent_record(self, obj: dict[str, object]) -> None:
        if self._claude_code_version is not None:
            return
        version: object = obj.get("version")
        if isinstance(version, str) and version:
            self._claude_code_version = version

    def note_completion(self, *, tool_use_id: str) -> bool:
        if not tool_use_id:
            return False
        with self._tails_lock:
            self._completed_dispatch_ids.add(tool_use_id)
            return self._drop_child_for_tool_use_id(tool_use_id)

    def _drop_child_for_tool_use_id(self, tool_use_id: str) -> bool:
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
        return self._thread is not None and self._thread.is_alive()

    @property
    def claude_code_version(self) -> str | None:
        return self._claude_code_version

    def note_dispatch(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
    ) -> None:
        if tool_name not in SUBAGENT_DISPATCH_TOOLS:
            return
        if tool_use_id in self._probed_dispatch_ids:
            return
        self._probed_dispatch_ids.add(tool_use_id)
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
        with self._tails_lock:
            if self._stopped:
                return
            self._stopped = True
        self._monitor_stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_seconds)
        with self._tails_lock:
            for _key, (transcript_path, _meta, _meta_dict, file_obj, _parser, _offset) in list(
                self._tails.items()
            ):
                with contextlib.suppress(Exception):
                    file_obj.close()
                del transcript_path
            self._tails.clear()

    def wait(self, *, timeout_seconds: float | None = None) -> None:
        if self._thread is None:
            return
        self._thread.join(timeout=timeout_seconds)

    def _run(self) -> None:
        try:
            while not self._monitor_stop.is_set():
                self._discover_new_files()
                self._advance_all_tails()
                if self._monitor_stop.wait(self._poll_interval):
                    break
        except Exception:
            return

    def _discover_new_files(self) -> None:
        with self._tails_lock:
            for transcript_path, meta_path in find_claude_subagent_transcripts(self._session_id):
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
                        continue
                parser = ClaudeInteractiveTranscriptParser()
                try:
                    file_obj = transcript_path.open("r", encoding="utf-8", errors="replace")
                except OSError:
                    continue
                self._tails[key] = (transcript_path, meta_path, meta_dict, file_obj, parser, 0)

    def _advance_all_tails(self) -> None:
        with self._tails_lock:
            for key in list(self._tails.keys()):
                entry = self._tails[key]
                transcript_path, _meta_path, meta_dict, file_obj, parser, offset = entry
                try:
                    file_obj.seek(0, 2)
                    end = file_obj.tell()
                    if end < offset:
                        file_obj.seek(0)
                        offset = 0
                    if end > offset:
                        file_obj.seek(offset)
                        chunk = file_obj.read(end - offset)
                        offset = end
                        self._forward_chunk(parser, chunk, transcript_path, meta_dict)
                except OSError:
                    continue
                self._tails[key] = (
                    transcript_path,
                    _meta_path,
                    meta_dict,
                    file_obj,
                    parser,
                    offset,
                )

    def _forward_chunk(
        self,
        parser: ClaudeInteractiveTranscriptParser,
        chunk: str,
        transcript_path: Path,
        meta_dict: dict[str, object] | None,
    ) -> None:
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
    return time.monotonic()


def _safe_metadata_get(metadata: object, key: str, default: object) -> object:
    if not hasattr(metadata, "get"):
        return default
    result: object = metadata.get(key, default)
    return result


def _summarize_tool_kind(event: object, prefix: str) -> str | None:
    metadata: object = getattr(event, "metadata", None)
    tool_obj: object = _safe_metadata_get(metadata, "tool", "Subagent")
    tool_name = str(tool_obj)
    return f"{prefix}:{tool_name}"


def _summarize_text_kind(event: object, prefix: str) -> str | None:
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


_summarize_event = summarize_event


__all__ = [
    "SUBAGENT_DISPATCH_TOOLS",
    "ClaudeSubagentTranscriptTails",
    "R7AbsentLayoutDiagnostic",
    "read_meta_file",
    "summarize_event",
]
