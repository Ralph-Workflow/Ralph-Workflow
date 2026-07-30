"""Black-box tests for the engine file-sink buffering invariant.

Both engine file sinks in ``ralph/logging.py`` -- the always-on
``ralph.log`` text sink and the structured ``ralph.jsonl`` sink --
must be created with an explicit ``buffering=8192`` kwarg so loguru's
``FileSink`` does not fall back to its ``buffering=1`` line-buffered
default (which would emit one OS write per log record and therefore
one fsevents notification per record at verbose levels). The
buffering invariant is the fseventsd-mitigation contract that
prevents the per-record filesystem-mutation class on long verbose
runs.

These tests are fully black-box: they inject a recording fake
``sink_adder`` into ``configure_logging`` and assert on the
arguments the production code passed to it. No real loguru file
I/O occurs -- the fake ``sink_adder`` replaces ``logger.add`` for
the file-sink wiring, the console sink is wired through real
``logger.add`` but never invoked, and ``tmp_path`` is only used
for the run-directory ``mkdir`` (which is the production behavior).
No ``time.sleep``, no real ``subprocess``, no test-file
suppressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph.logging import configure_logging

_FILE_SINK_BUFFER_BYTES: int = 8192


@dataclass(frozen=True)
class _RecordedSinkAddition:
    """One ``sink_adder`` invocation captured by the fake recorder."""

    target: object
    kwargs: dict[str, object]


class _RecordingSinkAdder:
    """Drop-in fake for ``loguru.Logger.add`` that records every call.

    Mirrors the ``Callable[..., int]`` shape so ``configure_logging``
    accepts it via the ``sink_adder`` parameter. Returns a synthetic
    monotonic sink id so callers that call ``logger.remove(sink_id)``
    (none in this test path) would see a real ``int``.
    """

    def __init__(self) -> None:
        self.calls: list[_RecordedSinkAddition] = []
        self._next_sink_id: int = 1000

    def __call__(self, target: object, *args: object, **kwargs: object) -> int:
        del args
        merged: dict[str, object] = dict(kwargs)
        self.calls.append(_RecordedSinkAddition(target=target, kwargs=merged))
        sink_id: int = self._next_sink_id
        self._next_sink_id += 1
        return sink_id


def _path_ends_with(call: _RecordedSinkAddition, suffix: str) -> bool:
    """Return True iff the recorded target ends with ``suffix``.

    Matches both ``str`` and ``pathlib.Path`` targets by coercing to
    ``str`` -- ``Path("/tmp/x/ralph.log")`` endswith ``"ralph.log"``
    via ``str(path)``.
    """
    target_str: str = str(call.target)
    return target_str.endswith(suffix)


def _find_call_for(
    calls: list[_RecordedSinkAddition], suffix: str
) -> _RecordedSinkAddition | None:
    """Return the first recorded call whose target ends with ``suffix``."""
    for call in calls:
        if _path_ends_with(call, suffix):
            return call
    return None


def test_engine_file_sinks_are_block_buffered(tmp_path: Path) -> None:
    """Both engine file sinks (ralph.log and ralph.jsonl) MUST be passed
    ``buffering=8192`` via the injected fake sink-adder, with no real
    loguru file I/O, and all other kwargs preserved byte-for-byte."""
    adder: _RecordingSinkAdder = _RecordingSinkAdder()

    session = configure_logging(
        verbosity=2,
        log_directory=tmp_path,
        run_id="run-block-buf",
        structured=True,
        sink_adder=adder,
    )

    # AC-03: exactly TWO file-sink additions were recorded (ralph.log
    # + ralph.jsonl). A third sink would leak a per-record fsevents
    # source -- this assertion pins the count invariant.
    assert len(adder.calls) == 2, (
        f"expected exactly two sink-adder calls (ralph.log + ralph.jsonl);"
        f" got {len(adder.calls)}: {[call.target for call in adder.calls]}"
    )

    text_call: _RecordedSinkAddition | None = _find_call_for(adder.calls, "ralph.log")
    structured_call: _RecordedSinkAddition | None = _find_call_for(adder.calls, "ralph.jsonl")

    assert text_call is not None, (
        f"expected exactly one ralph.log sink addition; got targets:"
        f" {[call.target for call in adder.calls]}"
    )
    assert structured_call is not None, (
        f"expected exactly one ralph.jsonl sink addition; got targets:"
        f" {[call.target for call in adder.calls]}"
    )

    # Both file sinks MUST be block-buffered with buffering=8192.
    assert text_call.kwargs.get("buffering") == _FILE_SINK_BUFFER_BYTES, (
        f"ralph.log sink must be passed buffering={_FILE_SINK_BUFFER_BYTES}; got"
        f" {text_call.kwargs.get('buffering')!r}"
    )
    assert structured_call.kwargs.get("buffering") == _FILE_SINK_BUFFER_BYTES, (
        f"ralph.jsonl sink must be passed buffering={_FILE_SINK_BUFFER_BYTES}; got"
        f" {structured_call.kwargs.get('buffering')!r}"
    )

    # ralph.log kwargs preserved byte-for-byte (besides the added buffering).
    assert text_call.kwargs.get("level") == "INFO", (
        f"ralph.log level kwarg changed: {text_call.kwargs.get('level')!r}"
    )
    expected_text_format: str = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}"
    assert text_call.kwargs.get("format") == expected_text_format, (
        f"ralph.log format kwarg changed: {text_call.kwargs.get('format')!r}"
    )
    assert text_call.kwargs.get("colorize") is False, (
        f"ralph.log colorize kwarg changed: {text_call.kwargs.get('colorize')!r}"
    )
    assert text_call.kwargs.get("backtrace") is True, (
        f"ralph.log backtrace kwarg changed: {text_call.kwargs.get('backtrace')!r}"
    )
    assert text_call.kwargs.get("diagnose") is False, (
        f"ralph.log diagnose kwarg changed: {text_call.kwargs.get('diagnose')!r}"
    )
    assert text_call.kwargs.get("rotation") == "10 MB", (
        f"ralph.log rotation kwarg changed: {text_call.kwargs.get('rotation')!r}"
    )

    # ralph.jsonl kwargs preserved byte-for-byte (besides the added buffering).
    assert structured_call.kwargs.get("serialize") is True, (
        f"ralph.jsonl serialize kwarg changed:"
        f" {structured_call.kwargs.get('serialize')!r}"
    )
    assert structured_call.kwargs.get("level") == "INFO", (
        f"ralph.jsonl level kwarg changed: {structured_call.kwargs.get('level')!r}"
    )
    assert structured_call.kwargs.get("backtrace") is True, (
        f"ralph.jsonl backtrace kwarg changed:"
        f" {structured_call.kwargs.get('backtrace')!r}"
    )
    assert structured_call.kwargs.get("diagnose") is False, (
        f"ralph.jsonl diagnose kwarg changed:"
        f" {structured_call.kwargs.get('diagnose')!r}"
    )
    assert structured_call.kwargs.get("rotation") == "10 MB", (
        f"ralph.jsonl rotation kwarg changed:"
        f" {structured_call.kwargs.get('rotation')!r}"
    )

    # No real loguru file handler was created for either path -- the
    # fake sink-adder captured every file-sink call.  The ralph.log /
    # ralph.jsonl files must NOT exist on disk because no real
    # ``logger.add(<file-path>, ...)`` ran.
    text_log_path = session.paths.text_log_path
    structured_log_path = session.paths.structured_log_path
    assert text_log_path is not None
    assert structured_log_path is not None
    assert not text_log_path.exists(), (
        "ralph.log must NOT exist on disk when sink_adder is injected"
        " (no real loguru file handler ran)"
    )
    assert not structured_log_path.exists(), (
        "ralph.jsonl must NOT exist on disk when sink_adder is injected"
        " (no real loguru file handler ran)"
    )

    # The run directory was still created (production behavior -- the
    # run_directory.mkdir is unconditional so operators can drop
    # artifacts alongside the log file).
    assert session.paths.run_directory is not None
    assert session.paths.run_directory.exists()


def test_ralph_log_text_sink_is_buffered_when_structured_disabled(tmp_path: Path) -> None:
    """When ``structured=False``, exactly ONE sink-adder call (the
    ralph.log text sink) is recorded -- and it MUST still be passed
    ``buffering=8192``. Proves the buffering invariant is not
    accidentally gated on the structured branch."""
    adder: _RecordingSinkAdder = _RecordingSinkAdder()

    session = configure_logging(
        verbosity=3,
        log_directory=tmp_path,
        run_id="run-text-only",
        structured=False,
        sink_adder=adder,
    )

    # Only the ralph.log text sink is added when structured=False;
    # the ralph.jsonl structured sink MUST NOT be recorded.
    assert len(adder.calls) == 1, (
        f"expected exactly one sink-adder call (ralph.log only);"
        f" got {len(adder.calls)}: {[call.target for call in adder.calls]}"
    )

    text_call: _RecordedSinkAddition | None = _find_call_for(adder.calls, "ralph.log")
    structured_call: _RecordedSinkAddition | None = _find_call_for(
        adder.calls, "ralph.jsonl"
    )

    assert text_call is not None, (
        f"expected ralph.log sink addition even when structured=False;"
        f" got: {[call.target for call in adder.calls]}"
    )
    assert text_call.kwargs.get("buffering") == _FILE_SINK_BUFFER_BYTES, (
        f"ralph.log sink must be passed buffering={_FILE_SINK_BUFFER_BYTES}"
        f" when structured=False; got"
        f" {text_call.kwargs.get('buffering')!r}"
    )
    assert structured_call is None, (
        f"ralph.jsonl sink must NOT be added when structured=False;"
        f" got target: {structured_call.target if structured_call else None!r}"
    )

    # Verbosity 3 maps to DEBUG.
    assert text_call.kwargs.get("level") == "DEBUG"

    # structured_log_path must be None in the session (the production
    # contract).
    assert session.paths.structured_log_path is None
    assert session.paths.text_log_path is not None
