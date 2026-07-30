"""Text-first rendered-record writer (wt-028-display S-13 / AC-11).

The verbatim ``.agent/raw/<safe_id>.log`` capture remains the source
of truth: every event the agent emits lands there untouched, and the
content condenser cites that file when it condenses oversized lines.
The **rendered** record is the parallel human-readable file an
operator reaches for once the terminal is closed and scrollback has
overflowed: one entry per event, coalesced reasoning, single
identity, indentation-based hierarchy, no color codes, stable field
order, safe to grep and redirect.

Two consumers feed the same stream of canonical
:class:`~ralph.display.agent_event_renderer.PresentedEntry` records:

* the live display (Rich color + glyphs + spinner residue),
* this writer (plain text, ``[hh:mm:ss] phase cycle=N iter=N/M
  agent=foo`` line format).

Adding a new event kind means adding one renderer; both consumers
inherit it. The writer does not know about Rich, so it cannot
accidentally leak a live-display artifact into the run record.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

#: Default bound for the in-memory ring buffer. Sized so a chatty
#: agent cannot exhaust the writer's heap on a long unattended run,
#: while still buffering enough entries to amortise the file flush.
#: The bound is enforced by ``collections.deque(maxlen=...)`` per
#: ``audit_resource_lifecycle`` so the accumulator is fail-closed.
#: DA-003 (wt-028-display): a chatty long-running run may produce
#: more than ``_DEFAULT_BUFFER_CAP`` entries between two explicit
#: ``flush()`` calls; ``append()`` flushes eagerly when the buffer
#: reaches ``_BUFFER_FLUSH_THRESHOLD`` so the ``deque`` never
#: silently evicts an accepted entry.
_DEFAULT_BUFFER_CAP: Final[int] = 512

#: Threshold for the eager-flush guard in ``append()``. Fires when
#: the buffer has this many entries waiting, leaving room for a few
#: more before the deque evicts its oldest item. The gap between
#: ``_BUFFER_FLUSH_THRESHOLD`` and ``_DEFAULT_BUFFER_CAP`` is small
#: so the flush overhead stays amortised into normal file-write cost.
_BUFFER_FLUSH_THRESHOLD: Final[int] = 480

#: Length of the ``hh:mm:ss`` timestamp portion extracted from an
#: ISO-8601 timestamp. Eight characters: hh, ``:``, mm, ``:``, ss.
_HHMMSS_LEN: Final[int] = 8

#: P1 (wt-028-display S-12 / AC-07): the record carries real
#: hierarchy via indentation. Two-space indent per ``indent_level``
#: keeps the file diff-friendly; the field column starts at column
#: 0 and the body hangs at the level column for the role. A
#: phase header is rendered as a level-0 line whose body is the
#: phase rule; condensation markers stay at the body level of
#: the entry they replace.
_INDENT_WIDTH: Final[int] = 2

#: ANSI / CSI escape sequence stripper for the rendered record. The
#: writer is the destination for the human-readable file; a stray
#: color code from upstream (the agent payload, a model-side escape
#: sequence) must never leak into the greppable record. Same family
#: of patterns as ``status_bar.py``'s ``_SAFE_LINE_ESCAPE_RE``.
_ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class _SafeId:
    """A filesystem-safe identity derived from ``agent`` / ``model`` strings.

    The verbatim capture uses the same id (``safe_id``) so the two
    files land side by side under ``.agent/raw/``. The id strips
    any character outside ``[A-Za-z0-9._-]`` and replaces runs of
    unsafe characters with a single underscore so the path is
    predictable across agents / models.
    """

    raw: str

    @property
    def value(self) -> str:
        """Return the safe filename component."""
        out: list[str] = []
        prev_underscore = False
        for ch in self.raw.strip():
            if ch.isalnum() or ch in ".-":
                out.append(ch)
                prev_underscore = False
            elif not prev_underscore:
                out.append("_")
                prev_underscore = True
        safe = "".join(out).strip("_")
        return safe or "unknown"


def safe_id_for(agent: str, model: str | None = None) -> str:
    """Return the safe filename component for an ``(agent, model)`` pair.

    The component is what :class:`RenderedRecordWriter` appends to
    ``.agent/raw/<component>.rendered.log``. Two agents that share a
    model therefore produce distinct files (``claude_minimax-M3``
    vs ``pi_minimax-M3``); two runs of the same agent / model share
    the same file (``safe_id_for("claude", "minimax-M3")`` is stable
    across the project lifetime).
    """
    if model:
        return _SafeId(f"{agent}_{model}").value
    return _SafeId(agent).value


def rendered_record_path(workspace_root: Path, agent: str, model: str | None = None) -> Path:
    """Return the absolute path of the rendered record for ``agent``/``model``."""
    root = Path(workspace_root) / ".agent" / "raw"
    return root / f"{safe_id_for(agent, model)}.rendered.log"


def _format_entry_line(entry: object) -> str:
    """Render one :class:`PresentedEntry` as a plain-text entry with hanging continuations.

    The formatter reads only the public-ish attributes of the entry
    (``timestamp``, ``phase``, ``cycle``, ``iter``, ``agent``,
    ``severity``, ``body``) and assembles a fixed-order
    ``[hh:mm:ss] phase cycle=N iter=N/M agent=foo severity=info
    body`` entry. The first body line shares the header; subsequent
    body lines hang beneath it with tabs normalized to spaces so file
    excerpts retain their structure in the greppable record.

    P1 (wt-028-display S-12 / AC-07): the line opens with
    ``indent_level`` copies of :data:`_INDENT_WIDTH` so a tool
    result visibly hangs under its call (level 1) and a phase
    header sits at the margin (level 0). The indent is part of
    the contract: a reader (or a screen reader / braille display
    that linearises the file) sees the structural position from
    the leading whitespace alone, with the grouping role
    available via ``grouping_role`` for consumers that want the
    semantic label.

    The function is import-tolerant: it tolerates any object that
    exposes the expected attributes (the live-display presenter and
    the canonical pipeline both produce such objects) and falls back
    to ``str(entry)`` when the shape is unknown so a future event
    kind cannot crash the writer.
    """
    timestamp = _strip_ansi(_extract_field(entry, "timestamp"))
    phase = _strip_ansi(_extract_field(entry, "phase"))
    cycle = _strip_ansi(_extract_field(entry, "cycle"))
    iter_ = _strip_ansi(_extract_field(entry, "iter"))
    agent = _strip_ansi(_extract_field(entry, "agent"))
    severity = _strip_ansi(_extract_field(entry, "severity"))
    body = _extract_field(entry, "body")
    indent_level = _extract_indent_level(entry)
    grouping_role = _strip_ansi(_extract_field(entry, "grouping_role")) or "agent_text"

    hh_mm_ss = _to_hh_mm_ss(timestamp)
    indent_prefix = " " * (_INDENT_WIDTH * max(0, indent_level))
    parts = [f"[{hh_mm_ss}]"]
    if grouping_role == "phase_header":
        if phase:
            from ralph.display.parallel_display import phase_label

            parts.append(phase_label(phase))
        if cycle:
            parts.append(f"cycle={cycle}")
        if iter_:
            parts.append(f"iter={iter_}")
        if agent:
            parts.append(f"agent={agent}")
    else:
        # Phase context belongs to its header; event rows carry only what changed.
        if severity and severity != "info":
            parts.append(f"severity={severity}")
        body_str = _flatten_body(body)
        if body_str:
            parts.append(body_str)
    parts.append(f"role={grouping_role}")
    first_line = indent_prefix + " ".join(parts)
    continuation_prefix = " " * (_INDENT_WIDTH * (max(0, indent_level) + 1))
    body_lines = _body_lines(body)
    return "\n".join((first_line, *(continuation_prefix + line for line in body_lines[1:])))


def _strip_ansi(value: object) -> str:
    """Return a string field safe for the plain-text record."""
    if value is None:
        return ""
    try:
        return _ANSI_ESCAPE_RE.sub("", str(value))
    except Exception:
        return ""


def _extract_field(entry: object, name: str) -> object:
    """Return ``entry.<name>`` when present, else ``None``.

    Tolerant of dict-like and object-like entries; the canonical
    presenter returns dataclass instances, but the writer also
    accepts ``dict`` so a future migration to typed-event records
    does not require rewriting this helper.
    """
    if entry is None:
        return None
    if isinstance(entry, dict):
        value: object = entry.get(name)
        return value
    raw: object = getattr(entry, name, None)
    return raw


def _extract_indent_level(entry: object) -> int:
    """Return the structured indent level (default 0) for ``entry``.

    P1 (wt-028-display S-12 / AC-07): the canonical
    :class:`PresentedEntry` carries an ``indent_level`` integer
    that controls the line's leading whitespace. Tolerates a
    non-int value (clamped to 0) so a future event kind cannot
    crash the writer; a missing field defaults to 0 to keep
    pre-S-12 entries on the same line they always rendered at.
    """
    raw = _extract_field(entry, "indent_level")
    if isinstance(raw, bool):
        # ``bool`` is a subclass of ``int`` in Python; refuse so a
        # accidental ``indent_level=True`` doesn't render as
        # one full level of indent.
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    return 0


def _to_hh_mm_ss(timestamp: object) -> str:
    """Format an ISO-8601 timestamp as ``hh:mm:ss`` (UTC)."""
    if not timestamp:
        return "??:??:??"
    try:
        text = str(timestamp)
    except Exception:
        return "??:??:??"
    # Accept ``YYYY-MM-DDThh:mm:ss[.ffffff][+hh:mm]`` and friends.
    try:
        head = text.split("T", 1)[1] if "T" in text else text
        time_part = head.split("+", 1)[0].split("-", 1)[0].split("Z", 1)[0]
        hh_mm_ss = time_part[:_HHMMSS_LEN]
        if (
            len(hh_mm_ss) == _HHMMSS_LEN
            and hh_mm_ss[2] == ":"
            and hh_mm_ss[5] == ":"
        ):
            return hh_mm_ss
    except Exception:
        pass
    return "??:??:??"


def _body_lines(body: object) -> tuple[str, ...]:
    """Return normalized body lines for a text-first hanging-indent record."""
    if body is None:
        return ()
    text = body if isinstance(body, str) else " ".join(str(item) for item in body) if isinstance(body, Iterable) else str(body)
    return tuple(
        _ANSI_ESCAPE_RE.sub("", line.replace("\t", " ")).rstrip()
        for line in text.replace("\r\n", "\n").split("\n")
    )


def _flatten_body(body: object) -> str:
    """Return the first normalized body line for the entry header."""
    return _body_lines(body)[0].strip() if _body_lines(body) else ""


class RenderedRecordWriter:
    """Bounded text-first writer for the ``.agent/raw/<id>.rendered.log`` file.

    Lifecycle (matches the ``RawOverflowLog`` pattern: bounded
    buffer, flush interval, silent-disable on I/O error):

    * ``append(entry)`` formats one entry to a bounded
      ``deque(maxlen=_DEFAULT_BUFFER_CAP)``. It flushes before the
      deque can evict an accepted line, so a chatty stream cannot
      exhaust memory or silently lose record entries.
    * ``flush()`` writes the buffered lines to disk under
      ``.agent/raw/<safe_id>.rendered.log`` and clears the buffer.
      Failures are caught and recorded via the optional
      ``on_error`` callback; subsequent writes continue silently so
      a transient disk error cannot crash the display path.
    * ``disable()`` permanently disables the writer; further
      ``append()`` calls are no-ops. This is the recovery path for
      a filesystem that has gone read-only.

    Concurrency: ``RenderedRecordWriter`` is thread-safe; the
    underlying ``deque`` operations are atomic and the file write
    is wrapped in a lock so a concurrent ``flush()`` from another
    thread cannot interleave a partial line.
    """

    __slots__ = (
        "_agent",
        "_buffer",
        "_disabled",
        "_lock",
        "_model",
        "_on_error",
        "_path",
        "_workspace_root",
    )

    def __init__(
        self,
        workspace_root: Path,
        agent: str,
        *,
        model: str | None = None,
        on_error: Callable[[Exception], object] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._agent = agent
        self._model = model
        self._path = rendered_record_path(self._workspace_root, agent, model)
        # bounded-accumulator-ok: deque(maxlen=512) per audit_resource_lifecycle
        self._buffer: deque[str] = deque(maxlen=512)
        self._disabled = False
        self._lock = threading.Lock()
        self._on_error = on_error

    @property
    def path(self) -> Path:
        """Return the absolute path of the rendered record file."""
        return self._path

    @property
    def disabled(self) -> bool:
        """Return ``True`` once ``disable()`` has been called."""
        return self._disabled

    @property
    def pending_lines(self) -> int:
        """Return the number of buffered lines not yet flushed to disk."""
        return len(self._buffer)

    @property
    def buffer_capacity(self) -> int:
        """Return the maximum number of entries the in-memory buffer can hold.

        DA-003 (wt-028-display): the in-memory ``deque`` lives behind
        a private ``_DEFAULT_BUFFER_CAP`` constant; tests and
        out-of-process consumers need a stable public name to size
        their stress probes (the regression case in
        ``tests/display/test_record_writer.py`` exercises
        ``cap + 1`` events to prove the eager-flush guard fires
        before the deque silently evicts an accepted entry).
        Returning the deque's own ``maxlen`` keeps the public
        property in lock-step with the actual buffer; the value
        is the same integer ``_DEFAULT_BUFFER_CAP`` would return.
        ``deque.maxlen`` is typed as ``int | None`` for unbounded
        deques; the writer constructs its deque with a finite cap
        (see ``__init__``), so the ``or _DEFAULT_BUFFER_CAP``
        fallback is unreachable in production but the type system
        needs a concrete ``int`` return path -- the fallback
        returns the canonical cap so the contract is never
        silently violated if a future change ever relaxes the
        deque bound.
        """
        # bounded-accumulator-ok: deque(maxlen=_DEFAULT_BUFFER_CAP) per
        # audit_resource_lifecycle; ``maxlen`` is the source of truth.
        return self._buffer.maxlen or _DEFAULT_BUFFER_CAP

    def append(self, entry: object) -> None:
        """Format ``entry`` and buffer the resulting line.

        DA-003 (wt-028-display): flush eagerly when the buffered
        count reaches ``_BUFFER_FLUSH_THRESHOLD`` so the underlying
        ``deque(maxlen=_DEFAULT_BUFFER_CAP)`` never silently evicts
        an accepted entry on a chatty long-running run. The eager
        flush reuses ``flush()`` -- which holds ``_lock`` only for
        the buffer swap and not for the file write -- so concurrent
        ``append()`` callers from other threads do not block on
        disk I/O. The lock is acquired once to add the line and
        decide whether to flush; the threshold check itself runs
        under the lock so a burst of concurrent ``append()`` calls
        cannot collectively push the buffer past ``_DEFAULT_BUFFER_CAP``
        between the check and the flush.
        """
        line = _format_entry_line(entry)
        should_flush = False
        with self._lock:
            if self._disabled:
                return
            self._buffer.append(line)
            if len(self._buffer) >= _BUFFER_FLUSH_THRESHOLD:
                should_flush = True
        if should_flush:
            self.flush()

    def extend(self, entries: Iterable[object]) -> None:
        """Buffer every entry in ``entries`` in order."""
        for entry in entries:
            self.append(entry)

    def flush(self) -> int:
        """Write the buffered lines to disk and clear the buffer.

        Returns the number of lines written. Failures are routed to
        the optional ``on_error`` callback (and silently swallowed)
        so the display path is never crashed by a transient disk
        error.
        """
        error: OSError | None = None
        with self._lock:
            if self._disabled or not self._buffer:
                return 0
            lines = list(self._buffer)
            self._buffer.clear()
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fp:
                    fp.write("\n".join(lines))
                    fp.write("\n")
            except OSError as exc:
                self._disabled = True
                error = exc
        if error is not None:
            if self._on_error is not None:
                with contextlib.suppress(Exception):
                    self._on_error(error)
            return 0
        return len(lines)

    def disable(self) -> None:
        """Permanently disable the writer."""
        with self._lock:
            self._disabled = True
            self._buffer.clear()

    def _set_path_for_testing(self, path: Path) -> None:
        """Replace the writer's output path (test-only).

        Tests use this to inject a path whose parent directory
        cannot be created (e.g. a regular file where a directory
        is expected) so the ``OSError``-on-flush path can be
        exercised without monkeypatching the private ``_path``
        attribute. Production code must use the constructor's
        ``workspace_root`` / ``agent`` / ``model`` to derive the
        path.
        """
        self._path = path


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 form (writer-local helper)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


__all__ = [
    "RenderedRecordWriter",
    "rendered_record_path",
    "safe_id_for",
    "utc_now_iso",
]


# ponytail: This module is intentionally minimal: one dataclass-free
# buffer, one formatter, one path helper. The integration seam into
# ``parallel_display.py`` is the ``append(entry)`` method, which any
# caller can invoke without a Rich dependency. The shape of
# ``PresentedEntry`` is documented at the top of the file; if the
# canonical presenter grows a new field, add it to
# the formatter so the rendered record's field order stays stable.
