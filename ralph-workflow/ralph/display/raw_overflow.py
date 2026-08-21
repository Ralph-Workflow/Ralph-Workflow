"""Per-unit raw NDJSON overflow log writer."""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import re
import shlex
import threading
import time
import weakref
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Final, cast

from loguru import logger

from ralph.display._raw_log_break import RawLogBreak

# Re-exported so the split of this module into writer + detector is not
# a breaking change for the callers that grade a capture they wrote.
from ralph.display.raw_log_breaks import (
    HARNESS_PTY_INPUT_ECHO_LINES,
    MAX_REPORTED_BREAKS,
    PTY_EXIT_COMMAND,
    TURN_BOUNDARY_MARKER,
    detect_raw_log_breaks,
    is_harness_input_echo,
    is_interactive_pty_transport,
    iter_capture_lines,
    nul_separated_chunks,
)
from ralph.display.record_writer import safe_id_for, safe_id_is_lossless

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import AgentConfig

#: Hex characters of the model-flag digest appended to an identity.
_ID_DIGEST_CHARS: Final = 6

#: Runs of anything that has no place in a filename component. Collapsed
#: to a single dash so two different flags cannot fold onto one identity.
_UNSAFE_ID_RUN: Final = re.compile(r"[^0-9A-Za-z._-]+")

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


def _disambiguated(unit_id: str, model: str | None) -> str:
    """Append a digest when the filename sanitiser would fold this identity.

    ``safe_id_for`` builds the filename from ``unit_id`` and ``model``,
    folding everything that is not alphanumeric, ``.`` or ``-`` into a
    single ``_`` and stripping ``_`` from the edges. So ``codex/a@b``,
    ``codex/a_b`` and ``codex/a:b`` all became ``codex_a_b.log`` --
    three agents writing one capture, each grading the others' bytes and
    quoting their transport failures.

    The test for "would this fold" comes FROM the sanitiser
    (:func:`safe_id_is_lossless`). Restating it as a second character
    class here is what let the defect survive a fifth time: that class
    counted ``_`` as safe, while the sanitiser collapses and strips it,
    so ``codex/gpt5`` and ``codex/_gpt5`` were judged distinct and
    landed in one file. It also called every non-ASCII letter unsafe and
    appended a digest to identities that never needed one.

    Applied at EVERY branch, not just the last one. Adding it only where
    the model-flag fallback runs left the headless-Claude and ``ccs``
    branches folding exactly as before, which is how this defect has
    survived four rounds of being "closed": each fix covered the family
    in front of it. The digest is taken over the raw pair, before any
    folding, and is added ONLY when folding would actually occur -- so
    an identity made of safe characters keeps the filename an operator
    already knows.
    """
    if safe_id_is_lossless(unit_id) and (model is None or safe_id_is_lossless(model)):
        return unit_id
    return f"{unit_id}-{_digest_of(f'{unit_id}\x00{model or ""}')}"


def _digest_of(raw: str) -> str:
    """Return the short digest that keeps two folded identities apart."""
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:_ID_DIGEST_CHARS]


def _model_from_flag(config: AgentConfig, model: str | None) -> str:
    """Return a filename-safe token for what ``model_flag`` adds to ``model``.

    ``model_flag`` is argv, not a value: ``--model anthropic/claude-4``,
    ``-m kimi-code/k3-256k``, ``--provider ollama --model llama3``,
    ``--model gpt-5.4 -c 'model_reasoning_effort = "high"'``. Its VALUE
    tokens are what distinguish two aliases of one executable.

    Values already carried by ``model`` are dropped, because the path
    appends that separately -- so an agent whose flag says only what
    ``model`` already says keeps the filename it had.
    """
    raw = cast("object", getattr(config, "model_flag", None))
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return ""
    values: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            # ``--model=x`` carries its value in the same token. Skipping
            # every dash-led token dropped it, and the identity fell back
            # to the bare executable.
            _, separator, tail = token.partition("=")
            if separator and tail:
                values.append(tail)
            continue
        values.append(token)
    distinguishing = [value for value in values if value != model]
    if not distinguishing:
        return ""
    readable = _UNSAFE_ID_RUN.sub("-", "-".join(distinguishing)).strip("-")
    # Readable token PLUS a digest of the exact flag. ``safe_id_for``
    # keeps only ``[0-9A-Za-z._-]`` and collapses runs, so distinct flags
    # fold onto one token -- ``--provider ollama --model llama3`` and
    # ``--provider ollama-llama3`` both read ``ollama-llama3``, and that
    # is two agents sharing a capture again. The digest restores
    # injectivity without costing an operator the ability to recognise
    # the file; it is taken over the raw flag, before any folding.
    digest = _digest_of(raw)
    return f"{readable}-{digest}" if readable else digest


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
        return _disambiguated("claude-headless", model)
    if executable.lower() in _DISPATCHER_EXECUTABLES:
        alias = next((token for token in tokens[1:] if not token.startswith("-")), "")
        if alias:
            return _disambiguated(f"{executable}-{alias}", model)
    # Whatever the COMMAND LINE says beyond ``config.model``. The capture
    # path is keyed ``(unit_id, config.model)``, but only some
    # dynamic-alias resolvers set ``model`` -- ``pi/``, ``cursor/``, ``kimi/``,
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
    #
    # It is NOT gated on ``model`` being absent. ``codex/<model>[effort=
    # high]`` sets ``model``, so a gated fallback never ran for it and
    # every effort variant of one codex model shared a capture -- a form
    # the shipped ralph-workflow.toml prints as an example. The flag is
    # consulted whenever it says something ``model`` does not.
    flag_model = _model_from_flag(config, model)
    if flag_model:
        return f"{executable}-{flag_model}"
    return _disambiguated(executable, model)


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

    def append_bytes(self, raw: bytes, *, counts_as_liveness: bool = True) -> bool:
        """Write *raw* to the overflow log without decoding it.

        The byte-faithful entry point. ``append`` takes ``str``, so a
        caller holding the agent's actual bytes had to decode them
        first, and every reader did that with ``errors="replace"`` --
        which rewrites a torn multi-byte sequence to U+FFFD before the
        capture ever sees it. A torn sequence is a byte-level
        fingerprint of the interleaved-write hazard this capture exists
        to make visible, and erasing it at write time left the detector
        grading a file that had already been cleaned up.
        """
        return self._write(raw.rstrip(b"\n") + b"\n", counts_as_liveness=counts_as_liveness)

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
        return self._write(
            (line.rstrip("\n") + "\n").encode("utf-8"),
            counts_as_liveness=counts_as_liveness,
        )

    def _write(self, encoded: bytes, *, counts_as_liveness: bool) -> bool:
        """Append already-encoded bytes. Shared by both entry points."""
        with self._lock:
            if self._disabled:
                return False
            try:
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
    "MAX_REPORTED_BREAKS",
    "PTY_EXIT_COMMAND",
    "TURN_BOUNDARY_MARKER",
    "RawLogBreak",
    "RawOverflowLog",
    "close_all_raw_overflow_logs",
    "detect_raw_log_breaks",
    "is_harness_input_echo",
    "is_interactive_pty_transport",
    "iter_capture_lines",
    "nul_separated_chunks",
    "raw_log_path_for",
]
