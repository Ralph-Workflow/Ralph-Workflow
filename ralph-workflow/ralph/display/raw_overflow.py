"""Per-unit raw NDJSON overflow log writer."""

from __future__ import annotations

import atexit
import contextlib
import json
import threading
import time
import weakref
from typing import TYPE_CHECKING, BinaryIO, Final, cast

from ralph.config.agent_transport import AgentTransport
from ralph.display._raw_log_break import RawLogBreak
from ralph.display.record_writer import safe_id_for
from ralph.display.vt_normalizer import normalize_vt_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DEFAULT_MAX_OVERFLOW_FILE_BYTES = 50 * 1024 * 1024
#: Userspace buffer for the persistent handle. Amortizes write syscalls
#: (and the fsevents they generate) across many appended lines.
_BUFFER_BYTES = 64 * 1024
#: Default seconds between forced flushes. MUST stay well below
#: ralph.timeout_defaults.LOG_GROWTH_SECONDS (30.0): operators tail this
#: file and the on-disk copy must never look wedged while the unit is live.
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0


#: Shared-by-path registry (S-8 / C4). Two callers constructing
#: ``RawOverflowLog`` for the same path (a reader-owned instance and a
#: display-owned instance, by far the common case) used to receive two
#: independent objects that shared ``self.path`` but neither lock nor
#: ``_first_write`` state -- whichever object's first ``append()`` ran
#: later opened the file in ``"wb"`` mode and truncated the other
#: object's already-written bytes, the plausible source of the
#: measured 2026-08-06 NUL-hole corruption. The registry keys on the
#: resolved path so all callers share one object per path.
#:
#: ``WeakValueDictionary`` (DA-001) keeps only a weak reference to the
#: stored instance, so when every strong reference (the
#: ``ParallelDisplay._overflow_logs`` slot, the reader's
#: ``self._raw_overflow`` slot, etc.) is dropped, the entry vanishes
#: automatically and the buffered file handle is closed via the
#: ``weakref.finalize`` hook registered in ``__init__``. A strong
#: ``dict`` reference here used to keep instances alive past their
#: owner's teardown, so a test or run that finished without calling
#: ``drop_unit`` / ``stop()`` reached interpreter finalization with
#: the file handle still open, triggering a ``ResourceWarning`` on
#: every affected test.
_REGISTRY_LOCK = threading.Lock()
_REGISTRY: weakref.WeakValueDictionary[str, RawOverflowLog] = (
    weakref.WeakValueDictionary()
)  # bounded-accumulator-ok: weak-keyed by definition; entries auto-evict when no strong references remain


#: Canonical harness-authored input lines that can appear verbatim in a
#: PTY transport's raw capture (measured live 2026-08-14, AGY smoke).
#: ``[claude turn boundary]`` is injected into the reader's line queue by
#: ``_pty_line_reader._request_interactive_exit`` / ``_sentinel_thread``
#: to delimit turns; ``/exit`` is typed into the agent's PTY stdin and
#: echoed back by the terminal line discipline. Both are Ralph-authored
#: harness input, not agent wire output — but they belong in the verbatim
#: capture, so the corruption detector must recognize them instead of
#: reporting a NON_JSONL break for every interactive-transport run.
TURN_BOUNDARY_MARKER: Final = "[claude turn boundary]"

#: The interactive-exit command the PTY line reader types into the agent
#: stdin at completion/stop time (echoed back into the capture).
PTY_EXIT_COMMAND: Final = "/exit"

#: Exact-match vocabulary of harness input lines tolerated by
#: :func:`detect_raw_log_breaks`. Exact match only: a line that merely
#: *contains* a marker is still graded, so an agent wire frame embedding
#: the marker text cannot smuggle a corrupted line past the detector.
HARNESS_PTY_INPUT_ECHO_LINES: frozenset[str] = frozenset(
    {TURN_BOUNDARY_MARKER, PTY_EXIT_COMMAND}
)


#: Measured AGY ``--print --output-format stream-json`` vendor status
#: vocabulary (live capture 2026-08-17, AGY v1.1.13
#: ``gemini-3.6-flash-low``). AGY's print mode emits one human-rendered
#: status line per completed tool call onto the same stdout/PTY the
#: stream-json frames arrive on, shaped
#: ``\u2713 PASS \u21b3 <tool_name> (<param summary>) <JSON tool result>``.
#: It is genuine vendor wire output -- the rendered sibling of the
#: stream-json ``tool`` DONE frame, not a corrupted or harness-authored
#: line -- so the corruption detector must recognize the prefix rather
#: than fail every live AGY smoke with ``raw transcript corrupted``
#: while the surrounding frames are intact. Prefix match only: a line
#: that does not START with the marker is still graded, so a corrupted
#: line merely *containing* the text cannot slip past.
AGY_PRINT_TOOL_RESULT_STATUS_PREFIX: Final = "\u2713 PASS \u21b3 "


def is_agy_print_tool_result_status_line(line: str) -> bool:
    """Return True when ``line`` is AGY's print-mode tool-result status line.

    Prefix match against
    :data:`AGY_PRINT_TOOL_RESULT_STATUS_PREFIX`; never a substring test.
    """
    return line.lstrip().startswith(AGY_PRINT_TOOL_RESULT_STATUS_PREFIX)


def is_harness_input_echo(line: str) -> bool:
    """Return True when ``line`` is a Ralph-authored harness input line.

    Exact (stripped) match against
    :data:`HARNESS_PTY_INPUT_ECHO_LINES`; never a substring test.
    """
    return line.strip() in HARNESS_PTY_INPUT_ECHO_LINES


def _is_canonical_transport_session_line(line: str) -> bool:
    """Return True when ``line`` is canonical PTY/session metadata.

    Interactive Claude emits human-readable session/resume/completion
    lines into the raw PTY capture (e.g. ``Session ID: <uuid>``).  They
    are not JSONL, but they are expected verbatim content -- not
    corruption.  ANSI/VT control codes are stripped before matching so
    TUI-styled banners are recognized.

    The canonical pattern vocabulary lives in
    ``ralph.agents.invoke._session`` and is imported here lazily to
    avoid an import-time cycle with ``ralph.agents.invoke`` (which
    imports this module for ``RawOverflowLog``).
    """
    from ralph.agents.invoke import is_canonical_session_text_line

    return is_canonical_session_text_line(normalize_vt_text(line).strip())


#: Transports whose raw capture is an interactive PTY stream rather than a
#: machine-readable JSONL protocol. For these transports visible tool
#: output, file contents, ANSI TUI redraws, and other human-readable text
#: are expected verbatim capture content, not corruption. NUL-byte
#: detection is still enforced (it signals the cross-writer truncation
#: hazard), but NON_JSONL grading is skipped so a healthy interactive run
#: is not mislabeled ``raw transcript corrupted``.
_INTERACTIVE_PTY_TRANSPORTS: frozenset[AgentTransport] = frozenset(
    {AgentTransport.CLAUDE_INTERACTIVE, AgentTransport.NANOCODER, AgentTransport.AGY}
)


def is_interactive_pty_transport(transport: AgentTransport | None) -> bool:
    """Return True when ``transport`` emits visible PTY output rather than JSONL."""
    return transport in _INTERACTIVE_PTY_TRANSPORTS


def raw_log_path_for(workspace_root: Path, unit_id: str, *, model: str | None = None) -> Path:
    """Return the on-disk path a real ``RawOverflowLog`` writer uses for this unit.

    S-4 (G4 / DoD 15): named so every caller that needs to *find* an
    already-written raw log (not just create/append to one) derives the
    exact same path the real writer used, instead of each caller
    re-deriving the ``safe_id_for(unit_id, model)`` formula inline and
    risking drift. Factored out of :func:`get_or_create_raw_overflow_log`,
    which now calls this helper instead of inlining the expression --
    a refactor, not a behavior change.
    """
    return workspace_root / ".agent" / "raw" / f"{safe_id_for(unit_id, model)}.log"


def get_or_create_raw_overflow_log(
    workspace_root: Path,
    unit_id: str,
    *,
    model: str | None = None,
    max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
    flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> RawOverflowLog:
    """Return the per-path ``RawOverflowLog`` for ``(workspace_root, unit_id, model)``.

    Process-wide per-path singleton: callers constructing
    ``RawOverflowLog`` for the same path receive the same instance so
    the lock and ``_first_write`` state are shared and a late first
    ``append()`` from one caller cannot truncate another caller's
    already-written bytes (S-8 / C4 / DoD 15). Returns the existing
    instance on a repeat call -- not a fresh one.
    """
    key_path = raw_log_path_for(workspace_root, unit_id, model=model)
    key = str(key_path.resolve(strict=False))
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(key)
        if existing is not None:
            return existing
        instance = RawOverflowLog(
            workspace_root,
            unit_id,
            model=model,
            max_bytes=max_bytes,
            flush_interval_seconds=flush_interval_seconds,
            now=now,
        )
        _REGISTRY[key] = instance
        return instance


def _forget_raw_overflow_log(key_path: str) -> None:
    """Drop one entry from the registry. Used by tests; not part of the public API."""
    with _REGISTRY_LOCK:
        _REGISTRY.pop(key_path, None)


def _finalize_close_raw_overflow_log(
    weak_self: weakref.ref[RawOverflowLog],
) -> None:
    """Module-level ``weakref.finalize`` callback (DA-001).

    Closes the buffered file handle when the owning ``RawOverflowLog``
    is garbage-collected. Receives a weak reference to ``self`` rather
    than capturing ``self`` via closure (which would create a strong
    reference cycle and defeat ``weakref.finalize``).
    """
    instance = weak_self()
    if instance is None:
        return
    with contextlib.suppress(Exception):
        instance.close()
    with contextlib.suppress(Exception):
        instance.disable()


def close_all_raw_overflow_logs() -> None:
    """Close and forget every registered ``RawOverflowLog``.

    DA-001: explicit teardown for callers that want a deterministic
    lifecycle (tests, the ``atexit`` hook, run-end coordinators that
    do not go through ``drop_unit`` / ``stop()``). Iterates a snapshot
    of the registry's strong references, closes each handle, then
    lets the ``WeakValueDictionary`` drop the dead entries naturally
    on the next ``gc.collect()``.

    Safe to call multiple times. Never raises.
    """
    snapshot: list[RawOverflowLog] = []
    with _REGISTRY_LOCK:
        snapshot = list(_REGISTRY.values())
    for instance in snapshot:
        with contextlib.suppress(Exception):
            instance.close()
        with contextlib.suppress(Exception):
            instance.disable()


#: ``atexit`` registration so any ``RawOverflowLog`` whose owning
#: display/run ended without an explicit ``close()`` /
#: ``drop_unit`` / ``stop()`` still gets its buffered handle closed
#: at interpreter shutdown, instead of triggering a ``ResourceWarning``
#: at finalization time (DA-001). Wrapped in ``contextlib.suppress``
#: so re-imports in test harnesses do not re-register the same hook.
with contextlib.suppress(ValueError):
    atexit.register(close_all_raw_overflow_logs)


def detect_raw_log_breaks(
    raw_path: Path, *, transport: AgentTransport | None = None
) -> list[RawLogBreak]:
    """Read ``raw_path`` back as JSONL and return every corruption break.

    S-8 / C4 / DoD 15: a corrupted or truncated transcript is a reported
    break, not a silent skip. Two break shapes are detected:

    - ``NUL_BYTES``: any NUL byte anywhere in the file. The parser
      cannot recover the next JSON frame's start (it cannot tell where
      the JSON ends, since JSON itself permits ``\\u0000`` as an
      escaped sequence inside a string but a bare NUL cannot appear in
      a well-formed JSON document on the wire).
    - ``NON_JSONL``: a line that is not a parseable JSON object. This
      catches the 2026-08-06 captured shape where a separate writer
      (the display layer's ``_get_overflow_log``) appended rendered
      ``\u2713 PASS\u2026`` text into the same verbatim capture.

    For interactive PTY transports (``claude_interactive``, ``nanocoder``)
    the raw capture is expected human-visible output rather than JSONL,
    so only ``NUL_BYTES`` and ``READ_ERROR`` breaks are reported. JSONL
    streams (headless Claude, AGY print mode, Codex, etc.) keep strict
    JSON-object validation for every line, with an allowlist for canonical
    session/resume/completion metadata lines emitted by the PTY/session
    layer.

    The function reads the file in binary mode so a NUL-byte break is
    observable. ``read_text(errors='replace')`` would silently swallow
    the NUL bytes; the binary read keeps the byte-level fingerprint
    visible.

    An absent file returns an empty break list (no break observed, no
    break reported). A read error (locked file, missing parent) is
    reported as a break with detail naming the OSError so the operator
    sees the I/O failure rather than a silent empty result.
    """
    breaks: list[RawLogBreak] = []
    if not raw_path.exists():
        return breaks
    try:
        payload = raw_path.read_bytes()
    except OSError as exc:
        return [
            RawLogBreak(
                kind="READ_ERROR",
                offset=0,
                detail=f"failed to read raw log: {exc}",
            )
        ]
    nul_offset = payload.find(b"\x00")
    if nul_offset >= 0:
        breaks.append(
            RawLogBreak(
                kind="NUL_BYTES",
                offset=nul_offset,
                detail=(
                    f"NUL-byte run begins at byte {nul_offset}; the "
                    "transcript is unparseable as JSONL past this point"
                ),
            )
        )
    if is_interactive_pty_transport(transport):
        return breaks
    return breaks + _detect_non_jsonl_breaks(payload)


def _detect_non_jsonl_breaks(payload: bytes) -> list[RawLogBreak]:
    """Return one ``NON_JSONL`` break per unparseable line.

    Splits the payload on NUL bytes first so a measured NUL-hole run
    does not silently swallow rendered text that follows the hole on
    the same line. Each chunk between NUL runs is then parsed as
    JSONL: lines that parse as JSON objects are skipped, and every
    other non-empty line (rendered ``\u2713 PASS\u2026`` text, control
    codes, malformed JSON) is a break.

    Canonical interactive-transport session/resume/completion metadata
    lines (see :func:`_is_canonical_transport_session_line`) are expected
    verbatim capture content, not corruption. They are recognized after
    VT/ANSI normalization so TUI-wrapped session lines are not misgraded.
    """
    breaks: list[RawLogBreak] = []
    offset = 0
    nul_chunks = payload.split(b"\x00")
    for chunk_index, chunk in enumerate(nul_chunks):
        # Advance offset past the chunk and (if not the last chunk)
        # past the NUL byte that terminated it.
        chunk_offset = offset
        offset += len(chunk)
        if chunk_index < len(nul_chunks) - 1:
            offset += 1  # the NUL byte itself
        for raw_line in chunk.splitlines(keepends=True):
            line_offset = chunk_offset
            chunk_offset += len(raw_line)
            line_bytes = raw_line.rstrip(b"\n").rstrip(b"\r")
            line_text = normalize_vt_text(
                line_bytes.decode("utf-8", errors="replace")
            ).strip()
            if not line_text:
                continue
            if is_harness_input_echo(line_text):
                # Ralph-authored harness input (see
                # :data:`HARNESS_PTY_INPUT_ECHO_LINES`): expected verbatim
                # capture content, not a corrupted or truncated frame.
                continue
            if is_agy_print_tool_result_status_line(line_text):
                # AGY vendor print-mode tool-result status line (see
                # :data:`AGY_PRINT_TOOL_RESULT_STATUS_PREFIX`): measured
                # wire output, not a corrupted frame.
                continue
            if _is_canonical_transport_session_line(line_text):
                # Canonical interactive-transport session/resume/completion
                # metadata (see :func:`_is_canonical_transport_session_line`):
                # emitted by the PTY/session layer, parsed by the session
                # extractor, and expected verbatim in the raw capture.
                continue
            try:
                parsed: object = json.loads(line_text)
            except json.JSONDecodeError:
                breaks.append(
                    RawLogBreak(
                        kind="NON_JSONL",
                        offset=line_offset,
                        detail=(
                            f"line at byte {line_offset} is not parseable "
                            f"JSON (first 60 chars: {line_text[:60]!r})"
                        ),
                    )
                )
                continue
            if not isinstance(parsed, dict):
                breaks.append(
                    RawLogBreak(
                        kind="NON_JSONL",
                        offset=line_offset,
                        detail=(
                            f"line at byte {line_offset} parses as JSON but "
                            f"is not a JSON object (type={type(parsed).__name__})"
                        ),
                    )
                )
    return breaks


class RawOverflowLog:
    """Append-mode raw log for a single work unit.

    Thread-safe. Holds one buffered file handle open for the unit's
    lifetime instead of opening/closing per line (the per-line pattern
    generated an fsevent storm on long runs). Silently no-ops on
    filesystem errors so the display path never crashes due to a
    read-only workspace.
    """

    def __init__(
        self,
        workspace_root: Path,
        unit_id: str,
        *,
        model: str | None = None,
        max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        # S-23 (wt-028-display): pair the verbatim capture with the
        # rendered record by deriving the file id from the same
        # ``safe_id_for(agent, model)`` helper. Without the model
        # suffix a mismatched pair (e.g. ``pi.log`` here, ``pi_X.log``
        # there) would orphan the rendered record's condensation
        # markers from the verbatim capture they point at.
        safe_id = safe_id_for(unit_id, model)
        self.path = workspace_root / ".agent" / "raw" / f"{safe_id}.log"
        self._lock = threading.Lock()
        self._first_write = True
        self._disabled = False
        self._max_bytes = max(max_bytes, 0)
        self._bytes_written = 0
        self._flush_interval = max(flush_interval_seconds, 0.0)
        self._now = now
        self._fh: BinaryIO | None = None
        self._last_flush = now()
        # DA-001: register a finalizer that closes the buffered file
        # handle when this instance is garbage-collected. The callback
        # receives a weak reference (no closure-captured ``self``),
        # so it does not create a strong reference cycle that would
        # prevent ``weakref.finalize`` from firing. Combined with the
        # ``WeakValueDictionary`` registry above, this ensures a test
        # or run that finishes without an explicit ``drop_unit`` /
        # ``stop()`` still releases its file handle before
        # interpreter shutdown, instead of triggering a
        # ``ResourceWarning`` at finalization.
        weakref.finalize(
            self,
            _finalize_close_raw_overflow_log,
            weakref.ref(self),
        )

    def disable(self) -> None:
        """Permanently disable this log so future appends are no-ops."""
        with self._lock:
            self._close_locked()
            self._disabled = True

    def append(self, line: str) -> bool:
        """Write *line* to the overflow log.

        Returns True when the line was written. Returns False when the log is
        disabled, the byte cap has been reached, or an I/O error occurs.
        """
        with self._lock:
            if self._disabled:
                return False
            try:
                text = line.rstrip("\n") + "\n"
                encoded = text.encode("utf-8")
                if self._bytes_written + len(encoded) > self._max_bytes:
                    self._close_locked()
                    self._disabled = True
                    return False
                if self._fh is None:
                    # filesystem-write-ok: bounded binary overflow stream directory creation
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    mode = "wb" if self._first_write else "ab"
                    # filesystem-write-ok: bounded binary overflow stream remains live until byte cap
                    handle_obj: object = self.path.open(mode, buffering=_BUFFER_BYTES)
                    self._fh = cast(
                        "BinaryIO", handle_obj
                    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
                    self._first_write = False
                fh: BinaryIO | None = self._fh
                if fh is None:
                    return False
                fh.write(encoded)
                self._bytes_written += len(encoded)
                if self._now() - self._last_flush >= self._flush_interval:
                    fh.flush()
                    self._last_flush = self._now()
                return True
            except (OSError, PermissionError):
                self._close_locked()
                self._disabled = True
                return False

    def flush(self) -> None:
        """Force buffered bytes to disk. Never raises."""
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._last_flush = self._now()
                except (OSError, PermissionError):
                    self._close_locked()
                    self._disabled = True

    def close(self) -> None:
        """Flush and release the file handle. Idempotent; appends may reopen."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._fh is not None:
            with contextlib.suppress(OSError, PermissionError):
                self._fh.close()
            self._fh = None

    def relative_reference(self, workspace_root: Path) -> str:
        """Return POSIX path relative to *workspace_root*, or absolute on error."""
        try:
            return self.path.relative_to(workspace_root).as_posix()
        except ValueError:
            return self.path.as_posix()

    @property
    def size_bytes(self) -> int:
        """Bytes appended so far (buffered bytes included).

        The idle watchdog's log-growth corroborator reads this to prove the
        unit is alive; it must advance on every append, not only on flush.
        Returns 0 before the first write. Never raises.

        The in-memory ``_bytes_written`` counter is the authoritative
        liveness signal — an on-disk ``stat()`` probe is intentionally
        avoided because a missing or unfetchable file (operator unlink,
        watcher quarantine, transient I/O error) must NOT silence the
        watchdog while the unit itself is still appending.
        """
        return self._bytes_written

    @property
    def is_disabled(self) -> bool:
        """True when the log has been permanently disabled (byte cap reached or I/O error)."""
        return self._disabled


__all__ = [
    "DEFAULT_FLUSH_INTERVAL_SECONDS",
    "DEFAULT_MAX_OVERFLOW_FILE_BYTES",
    "HARNESS_PTY_INPUT_ECHO_LINES",
    "PTY_EXIT_COMMAND",
    "TURN_BOUNDARY_MARKER",
    "RawLogBreak",
    "RawOverflowLog",
    "close_all_raw_overflow_logs",
    "detect_raw_log_breaks",
    "is_agy_print_tool_result_status_line",
    "is_harness_input_echo",
    "raw_log_path_for",
]
