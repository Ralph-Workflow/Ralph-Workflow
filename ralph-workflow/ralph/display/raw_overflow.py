"""Per-unit raw NDJSON overflow log writer."""

from __future__ import annotations

import atexit
import contextlib
import json
import shlex
import threading
import time
import weakref
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Final, cast

from loguru import logger

from ralph.config.agent_transport import AgentTransport
from ralph.display._raw_log_break import RawLogBreak
from ralph.display.record_writer import safe_id_for
from ralph.display.vt_normalizer import normalize_vt_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ralph.config.models import AgentConfig

DEFAULT_MAX_OVERFLOW_FILE_BYTES = 50 * 1024 * 1024
#: Userspace buffer for the persistent handle. Amortizes write syscalls
#: (and the fsevents they generate) across many appended lines.
_BUFFER_BYTES = 64 * 1024
#: Default seconds between forced flushes. MUST stay well below
#: ralph.timeout_defaults.LOG_GROWTH_SECONDS (30.0): operators tail this
#: file and the on-disk copy must never look wedged while the unit is live.
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0
#: Filename suffix of the DISPLAY-owned condensed log, the sibling of the
#: verbatim capture.
#:
#: The two used to be one file. The reader appended the agent's wire
#: JSONL and the display appended condensed tool-result / preview bodies
#: to the same path, so Ralph's own multi-line text landed as bare
#: non-JSON lines inside a JSONL stream and
#: :func:`detect_raw_log_breaks` read them back as agent corruption
#: (measured 2026-08-20: ``raw transcript corrupted: line at byte 157612
#: is not parseable JSON (first 60 chars: '---')`` -- the ``---`` front
#: matter of a markdown artifact the display had condensed).
#:
#: Splitting the files fixes that at the source rather than by escaping
#: around it: the verbatim capture holds only what the agent wrote, and
#: the condensed bodies stay plain readable text an operator can page
#: through. Both names end in ``.log`` so the ``.agent/raw/`` path policy
#: (``.log`` files only) still accepts them.
CONDENSED_LOG_SUFFIX: Final = ".overflow"


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
#: ``ParallelDisplay._condensed_logs`` slot, the reader's
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


#: Bytes already written to each raw-log path in THIS process, surviving
#: the writer instance that wrote them.
#:
#: A writer is per-acquisition: ``drop_unit`` forgets a unit's log, the
#: readers build one per agent invocation, and the weak registry evicts
#: an instance once nothing holds it. Keying "have we opened this?" and
#: "how much have we written?" to the instance therefore made attempt N
#: truncate attempt N-1's transcript and reset the byte cap, so a run's
#: capture held only its last invocation and the 50 MB ceiling could be
#: exceeded arbitrarily by re-acquiring.
#:
#: The state belongs to the FILE for the lifetime of the run, and a run
#: is a process, so it lives here. Guarded by its own lock because the
#: instance locks are per-writer and two paths can be opened at once.
_PATH_STATE_LOCK = threading.Lock()
_PATH_STATE: dict[str, int] = {}  # bounded-accumulator-ok: one entry per unit log per process
#: Paths whose byte-cap warning has already been emitted this process.
_CAP_WARNED: set[str] = set()  # bounded-accumulator-ok: one entry per unit log per process


def _resume_path_state(key: str) -> tuple[bool, int]:
    """Return ``(is_first_open, bytes_already_written)`` for ``key``."""
    with _PATH_STATE_LOCK:
        if key not in _PATH_STATE:
            return True, 0
        return False, _PATH_STATE[key]


def _record_path_bytes(key: str, total: int) -> None:
    """Record the running byte total for ``key``."""
    with _PATH_STATE_LOCK:
        _PATH_STATE[key] = total


def _claim_cap_warning(key: str) -> bool:
    """Return True the first time ``key`` reaches its cap in this process.

    The warning belongs to the FILE. Emitting it per writer meant one
    WARNING per agent invocation for the rest of a run once a capture
    filled up.
    """
    with _PATH_STATE_LOCK:
        if key in _CAP_WARNED:
            return False
        _CAP_WARNED.add(key)
        return True


def reset_raw_overflow_path_state() -> None:
    """Forget every path's write state. For tests that simulate a new run."""
    with _PATH_STATE_LOCK:
        _PATH_STATE.clear()
        _CAP_WARNED.clear()


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


# Executables whose FIRST POSITIONAL argument selects the agent runtime
# rather than a subcommand of one. ``ccs`` is a multiplexer: the registry
# synthesizes ``cmd="ccs <alias>"`` for every ``ccs/<alias>`` name (see
# ``registry._resolve_dynamic_ccs_agent``), so keying on the executable
# alone filed every alias under a single ``ccs.log`` -- the same
# two-agents-one-capture collision that gating the headless clause on
# ``claude`` was meant to close, moved rather than fixed. ``codex exec``
# is deliberately NOT here: ``exec`` is a subcommand of one runtime, so
# the executable already identifies it.
_DISPATCHER_EXECUTABLES: Final = frozenset({"ccs"})


def _model_from_flag(config: AgentConfig) -> str:
    """Return a filename-safe token for the model named in ``model_flag``.

    ``model_flag`` is argv, not a value: ``--model anthropic/claude-4``,
    ``-m kimi-code/k3-256k``, ``--provider ollama --model llama3``. The
    non-flag tokens are what distinguish two aliases of one executable.
    """
    raw = cast("object", getattr(config, "model_flag", None))
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return ""
    values = [token for token in tokens if not token.startswith("-")]
    return "-".join(values)


def raw_log_unit_id_for(config: AgentConfig) -> str:
    """Return the canonical raw-capture identity for an agent configuration.

    Headless Claude's ``-p`` / stream-JSON transport shares the ``claude``
    executable with interactive Claude, and every ``ccs/<alias>`` agent
    shares the ``ccs`` executable with every other. Both keep a
    distinguishing token in the identity so independently-written
    transcripts cannot collide on one file.
    Malformed commands preserve the readers' quiet failure behavior by
    returning an empty identity rather than raising.
    """
    try:
        tokens = shlex.split(config.cmd)
    except ValueError:
        return ""
    if not tokens:
        return ""
    raw_model = cast("object", getattr(config, "model", None))
    model = raw_model if isinstance(raw_model, str) and raw_model.strip() else None
    # The BASENAME, not the raw token. Agents are commonly invoked by
    # absolute path (``/opt/homebrew/bin/claude -p``) or through a
    # version manager's shim; keying on the raw token made those miss
    # every executable-gated clause below -- a path-invoked headless
    # Claude was filed as plain ``claude``, back into the interactive
    # agent's capture -- and put path separators into a filename.
    executable = PurePosixPath(tokens[0]).name or tokens[0]
    flags = set(tokens[1:]) | set((config.output_flag or "").split())
    # BOTH clauses are gated on the executable. ``--output-format=
    # stream-json`` is not unique to Claude -- kimi ships it as its
    # default output flag, and every ccs alias inherits it -- so an
    # ungated check filed those agents' transcripts under
    # ``claude-headless``, the same path headless Claude is graded on.
    # Two agents sharing one capture means one grades the other's
    # corruption and quotes the other's transport failures.
    if executable.lower() == "claude" and ("-p" in flags or "--output-format=stream-json" in flags):
        return "claude-headless"
    if executable.lower() in _DISPATCHER_EXECUTABLES:
        alias = next((token for token in tokens[1:] if not token.startswith("-")), "")
        if alias:
            return f"{executable}-{alias}"
    # Last resort: the model named on the COMMAND LINE. The capture path
    # is keyed ``(unit_id, config.model)``, but only some dynamic-alias
    # resolvers set ``model`` -- ``pi/``, ``cursor/``, ``kimi/``,
    # ``opencode/`` and ``nanocoder/`` set ``model_flag`` alone and leave
    # ``model`` as None. The key silently degenerated to
    # ``(executable,)``, so ``pi/anthropic/claude-sonnet-4-5`` and
    # ``pi/openai/gpt-5-codex`` both wrote ``pi.log``: one phase's
    # verdict grading another phase's bytes and quoting its transport
    # failures. The shipped ``ralph-workflow.toml`` documents exactly
    # these forms in ``[agent_chains]``, so this is the documented
    # configuration rather than an exotic one.
    #
    # Reading the flag here rather than fixing each resolver keeps the
    # rule in the layer that owns capture identity, and covers alias
    # families added later that forget ``model`` the same way.
    if model is None:
        flag_model = _model_from_flag(config)
        if flag_model:
            return f"{executable}-{flag_model}"
    return executable


def raw_log_path_for(
    workspace_root: Path,
    unit_id: str,
    *,
    model: str | None = None,
    condensed: bool = False,
) -> Path:
    """Return the on-disk path a real ``RawOverflowLog`` writer uses for this unit.

    S-4 (G4 / DoD 15): named so every caller that needs to *find* an
    already-written raw log (not just create/append to one) derives the
    exact same path the real writer used, instead of each caller
    re-deriving the ``safe_id_for(unit_id, model)`` formula inline and
    risking drift. Factored out of :func:`get_or_create_raw_overflow_log`,
    which now calls this helper instead of inlining the expression --
    a refactor, not a behavior change.

    ``condensed=True`` returns the DISPLAY-owned sibling
    (``<safe_id>.overflow.log``) instead of the verbatim capture. The two
    are separate files on purpose: the verbatim capture must stay a
    byte-faithful record of what the agent process wrote, and the
    display's condensed bodies are Ralph-authored text. Interleaving
    them made Ralph's own writes read back as agent corruption -- see
    :data:`CONDENSED_LOG_SUFFIX`.
    """
    suffix = CONDENSED_LOG_SUFFIX if condensed else ""
    return workspace_root / ".agent" / "raw" / f"{safe_id_for(unit_id, model)}{suffix}.log"


def get_or_create_raw_overflow_log(
    workspace_root: Path,
    unit_id: str,
    *,
    model: str | None = None,
    condensed: bool = False,
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

    Because a repeat call returns the existing instance, ``max_bytes``,
    ``flush_interval_seconds`` and ``now`` take effect only on the call
    that CREATES the writer; a later caller passing different values
    (a test injecting a fake clock, say) silently gets the first
    caller's.
    """
    key_path = raw_log_path_for(workspace_root, unit_id, model=model, condensed=condensed)
    key = str(key_path.resolve(strict=False))
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(key)
        if existing is not None:
            return existing
        instance = RawOverflowLog(
            workspace_root,
            unit_id,
            model=model,
            condensed=condensed,
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


def _finalize_close_raw_overflow_log(handle_box: list[BinaryIO | None]) -> None:
    """Module-level ``weakref.finalize`` callback (DA-001).

    Closes the buffered file handle when the owning ``RawOverflowLog``
    is garbage-collected.

    Receives the one-element list the instance keeps its handle in --
    NOT a weak reference to the instance. ``weakref.finalize`` runs
    AFTER the referent is cleared, so a weakref argument always resolves
    to ``None`` and the callback did nothing at all: the handle stayed
    open until CPython's buffered-writer destructor got to it, and the
    ``ResourceWarning`` this hook exists to prevent came back. The box is
    shared with the instance rather than closed over it, so the callback
    holds no strong reference to the owner.
    """
    handle = handle_box[0]
    if handle is None:
        return
    handle_box[0] = None
    with contextlib.suppress(OSError, ValueError):
        handle.close()


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
#: at finalization time (DA-001).
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
      catches the shape where rendered text reaches the verbatim
      capture -- historically the display layer wrote its condensed
      bodies to this same path, which is why a rendered
      ``\u2713 PASS\u2026`` row or a markdown ``---`` front-matter line
      could appear mid-stream. The display now owns a separate file
      (see :data:`CONDENSED_LOG_SUFFIX`), so a break here means the
      agent's own output is damaged.

    For interactive PTY transports (``claude_interactive``, ``nanocoder``,
    ``agy``)
    the raw capture is expected human-visible output rather than JSONL,
    so only ``NUL_BYTES`` and ``READ_ERROR`` breaks are reported. Other
    streams (headless Claude, Codex, etc.) keep strict JSON-object
    validation for every line, with an allowlist for canonical
    session/resume/completion metadata lines emitted by the PTY/session
    layer.

    The exemption is keyed on the TRANSPORT, and there is exactly one
    ``AgentTransport.AGY`` -- so an AGY run in print mode is exempt too,
    despite emitting JSONL. This paragraph previously claimed the
    opposite ("AGY print mode ... keeps strict JSON-object validation"),
    which no code path did. Stated as it is rather than as it reads
    better: AGY print-mode captures are NOT graded for ``NON_JSONL``,
    and a corrupt frame there is reported by nothing. Splitting the
    transport is the fix if that matters; narrowing the exemption
    without splitting it would make every interactive AGY run report
    its ordinary human-visible output as corruption.

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


#: Most breaks any one detection reports.
#:
#: Every caller uses ``breaks[0]`` -- the report names the first break
#: and its offset. Collecting the rest is pure cost, and on a badly
#: corrupted capture it is enormous: measured, a 10 MB file of short
#: non-JSON lines produced 5.2 million break objects in 119 s and 1.5 GB
#: of memory, at the 50 MB cap roughly ten minutes and 7 GB -- inside
#: the phase-close verdict, in exactly the case the detector exists for.
MAX_REPORTED_BREAKS: Final = 32


#: Window used to walk a NUL run at C speed rather than byte by byte.
_NUL_SKIP_WINDOW: Final = 1 << 16


def _skip_nul_run(payload: bytes, start: int) -> int:
    """Return the offset of the first non-NUL byte at or after ``start``.

    Walks in windows so the scan runs inside ``bytes.lstrip`` rather than
    a Python loop: a hole is millions of consecutive NULs, and stepping
    one byte at a time cost seconds on a capture at the file cap.
    """
    total = len(payload)
    while start < total:
        window = payload[start : start + _NUL_SKIP_WINDOW]
        stripped = window.lstrip(b"\x00")
        start += len(window) - len(stripped)
        if stripped:
            return start
    return total


def _iter_lines(chunk: bytes) -> Iterator[bytes]:
    """Yield ``chunk``'s lines lazily, keeping their terminators.

    ``bytes.splitlines`` materialises every line before the caller can
    stop, so the break cap could not bound it: 50 MB of short non-JSON
    lines allocated ~5.8 million objects and 356 MB before reporting its
    32 breaks -- inside the phase-close verdict, in the case the
    detector exists for.

    Splits on BOTH terminators, exactly as ``splitlines`` does. Matching
    only ``\n`` joined a bare-CR-separated line to its neighbour, and
    ``normalize_vt_text``'s carriage-return-overwrite semantics then
    erased the garbage before it could be graded -- so a corrupt line
    the old code reported went unseen. Measured on the field payload
    itself, one break became none: a perf change had silently blinded
    the detector this module exists to make trustworthy.
    """
    start = 0
    total = len(chunk)
    # Both terminator positions are CARRIED, not re-searched per line.
    # ``find`` scans to EOF when the byte is absent, and a healthy JSONL
    # capture contains no ``\r`` at all -- so searching for one on every
    # line made the scan O(lines x bytes). Measured on well-formed
    # stream-json frames: 2.9 s at 4 MB, 11.8 s at 8 MB, 38.1 s at 16 MB,
    # four times worse per doubling, extrapolating to minutes at the
    # 50 MB file cap. ``MAX_REPORTED_BREAKS`` cannot bound that, because
    # a healthy file has no breaks and the early return never fires --
    # and this runs on EVERY phase close, for every verdict label. A
    # carried position re-searches only after it is passed, so each byte
    # is visited once for each terminator.
    next_newline = chunk.find(b"\n")
    next_carriage = chunk.find(b"\r")
    while start < total:
        if 0 <= next_newline < start:
            next_newline = chunk.find(b"\n", start)
        if 0 <= next_carriage < start:
            next_carriage = chunk.find(b"\r", start)
        if next_newline < 0 and next_carriage < 0:
            yield chunk[start:]
            return
        if next_carriage < 0 or (0 <= next_newline < next_carriage):
            end = next_newline
        else:
            end = next_carriage
            # CRLF is ONE terminator, not two.
            if chunk[end + 1 : end + 2] == b"\n":
                end += 1
        yield chunk[start : end + 1]
        start = end + 1


def nul_separated_chunks(payload: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield ``(offset, chunk)`` for each NUL-delimited run in ``payload``.

    Splits LAZILY. ``payload.split(b"\\x00")`` materialises one bytes
    object per NUL byte before any grading happens, so a NUL hole -- the
    measured 2026-08-06 corruption shape, and precisely what this
    detector exists to find -- cost 7.9 s and 517 MB at the 50 MB file
    cap, none of it bounded by :data:`MAX_REPORTED_BREAKS` because the
    allocation ran first. Yielding slices lets the caller stop at the
    cap.
    """
    start = 0
    total = len(payload)
    while start <= total:
        nul_at = payload.find(b"\x00", start)
        if nul_at < 0:
            yield start, payload[start:]
            return
        if nul_at > start:
            yield start, payload[start:nul_at]
        # Skip the whole NUL RUN, not one byte of it. A hole is millions
        # of consecutive NULs, and stepping through it one at a time
        # yielded one empty chunk per byte.
        start = _skip_nul_run(payload, nul_at)


#: How much of a NON-JSON line the expensive grading path inspects.
#: Generous next to any marker it matches (the longest is a few dozen
#: bytes) and next to the 60 characters quoted in a break's detail.
_MAX_LINE_INSPECT_BYTES: Final = 64 * 1024


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
    for chunk_start, chunk in nul_separated_chunks(payload):
        if len(breaks) >= MAX_REPORTED_BREAKS:
            return breaks
        line_offset = chunk_start
        for raw_line in _iter_lines(chunk):
            if len(breaks) >= MAX_REPORTED_BREAKS:
                return breaks
            this_line_offset = line_offset
            line_offset += len(raw_line)
            # ONE copy, and no decode on the healthy path. Each of
            # ``rstrip`` / ``rstrip`` / ``decode`` / ``strip`` allocated
            # a full-size copy of the line before anything was graded,
            # so a single long frame cost roughly twenty times its own
            # size -- 490 MB transient on a 24 MB line, and 24 MB single
            # frames are measured real (see AGENT_STREAM_BUFFER_BYTES).
            # ``MAX_REPORTED_BREAKS`` bounds the NUMBER of lines graded,
            # never the cost of grading one.
            line_bytes = raw_line.strip()
            if not line_bytes:
                continue
            # Cheapest discriminator first. A healthy capture is almost
            # entirely well-formed frames, and VT normalisation plus the
            # canonical-session regex are far dearer than a parse that
            # succeeds -- running them on every line cost seconds per
            # call on a multi-megabyte healthy capture.
            try:
                # ``json.loads`` takes bytes directly, so a well-formed
                # frame -- almost every line of a healthy capture -- is
                # graded without ever materialising a decoded copy.
                parsed_fast: object = json.loads(line_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            else:
                if isinstance(parsed_fast, dict):
                    continue
            # Only a line that is NOT well-formed JSON reaches the dear
            # path, and only its head is inspected: the remaining checks
            # match short markers, and the detail text quotes 60
            # characters. The whole line was already offered to the
            # parser above, so truncating here cannot turn a valid frame
            # into a reported break.
            decoded = line_bytes[:_MAX_LINE_INSPECT_BYTES].decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            line_text = normalize_vt_text(decoded).strip()
            if not line_text:
                continue
            if is_harness_input_echo(line_text):
                # Ralph-authored harness input (see
                # :data:`HARNESS_PTY_INPUT_ECHO_LINES`): expected verbatim
                # capture content, not a corrupted or truncated frame.
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
                        offset=this_line_offset,
                        detail=(
                            f"line at byte {this_line_offset} is not parseable "
                            f"JSON (first 60 chars: {line_text[:60]!r})"
                        ),
                    )
                )
                continue
            if not isinstance(parsed, dict):
                breaks.append(
                    RawLogBreak(
                        kind="NON_JSONL",
                        offset=this_line_offset,
                        detail=(
                            f"line at byte {this_line_offset} parses as JSON but "
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
        condensed: bool = False,
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
        self.path = raw_log_path_for(workspace_root, unit_id, model=model, condensed=condensed)
        self._lock = threading.Lock()
        # Resolved, matching the registry's key. Keying the raw spelling
        # made the same file look like two files whenever the workspace
        # root arrived via a symlink or a relative path -- re-arming the
        # truncation and cap-reset this state exists to prevent.
        self._path_key = str(self.path.resolve(strict=False))
        first_open, already_written = _resume_path_state(self._path_key)
        # The first write of a RUN truncates; a later writer for the same
        # path continues it. See :data:`_PATH_STATE`.
        self._first_write = first_open
        self._disabled = False
        self._max_bytes = max(max_bytes, 0)
        # Bytes THIS writer has appended. The idle watchdog's log-growth
        # probe reads ``size_bytes`` and treats nonzero as "this
        # invocation has produced output", so it must start at zero even
        # when the file already holds an earlier invocation's bytes.
        self._bytes_written = 0
        # Bytes on the FILE, carried across writers so the byte cap
        # cannot be reset by re-acquiring the log.
        self._file_bytes = already_written
        self._flush_interval = max(flush_interval_seconds, 0.0)
        self._now = now
        # One-element box shared with the ``weakref.finalize`` callback,
        # which cannot reach ``self`` (see
        # :func:`_finalize_close_raw_overflow_log`).
        self._handle_box: list[BinaryIO | None] = [None]
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
            self._handle_box,
        )

    @property
    def _fh(self) -> BinaryIO | None:
        return self._handle_box[0]

    @_fh.setter
    def _fh(self, handle: BinaryIO | None) -> None:
        self._handle_box[0] = handle

    def disable(self) -> None:
        """Permanently disable this log so future appends are no-ops."""
        with self._lock:
            self._close_locked()
            self._disabled = True

    def append(self, line: str, *, counts_as_liveness: bool = True) -> bool:
        """Write *line* to the overflow log.

        Returns True when the line was written. Returns False when the log is
        disabled, the byte cap has been reached, or an I/O error occurs.

        ``counts_as_liveness=False`` writes the bytes but does NOT advance
        ``size_bytes``. The idle watchdog's log-growth probe reads
        ``size_bytes`` to answer "is this unit still making progress",
        and every other append happens on the CONSUMER side -- one per
        line the reader actually handed on. Queue-eviction writes come
        from the PRODUCER thread and mean the opposite: the consumer has
        fallen behind far enough to lose lines. Counting them would let
        a wedged consumer look alive for as long as the agent keeps
        talking, silencing the watchdog in precisely the stall it exists
        to catch. The transcript still gets the line; only the liveness
        claim is withheld.
        """
        with self._lock:
            if self._disabled:
                return False
            try:
                text = line.rstrip("\n") + "\n"
                encoded = text.encode("utf-8")
                if self._file_bytes + len(encoded) > self._max_bytes:
                    self._close_locked()
                    self._disabled = True
                    # Surfaced from the writer, not the display: the
                    # display only owns the condensed log, so once the
                    # verbatim capture moved out from under it the graded
                    # transcript could hit the cap and stop recording
                    # with no operator signal at all -- and the idle
                    # watchdog's log-growth probe reads ``is_disabled``,
                    # so it goes quiet at the same moment.
                    if _claim_cap_warning(self._path_key):
                        logger.warning(
                            "raw log {path} reached its {cap}-byte cap and is no "
                            "longer being written; the remainder of this unit's "
                            "output is not captured",
                            path=self.path,
                            cap=self._max_bytes,
                        )
                    return False
                if self._fh is None:
                    # filesystem-write-ok: bounded binary overflow stream directory creation
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    if not self._first_write and not self.path.exists():
                        # The file went away between writers; the carried
                        # total describes bytes that no longer exist.
                        self._file_bytes = 0
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
                if counts_as_liveness:
                    self._bytes_written += len(encoded)
                # The byte cap always counts these: they are real bytes
                # on the file regardless of what they prove.
                self._file_bytes += len(encoded)
                _record_path_bytes(self._path_key, self._file_bytes)
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
    "is_harness_input_echo",
    "nul_separated_chunks",
    "raw_log_path_for",
]
