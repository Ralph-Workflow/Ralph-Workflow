from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph.logging import configure_logging

_FILE_SINK_BUFFER_BYTES: int = 8192


@dataclass(frozen=True)
class _RecordedSinkAddition:
    target: object
    kwargs: dict[str, object]


class _RecordingSinkAdder:
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
    target_str: str = str(call.target)
    return target_str.endswith(suffix)


def _find_call_for(calls: list[_RecordedSinkAddition], suffix: str) -> _RecordedSinkAddition | None:
    for call in calls:
        if _path_ends_with(call, suffix):
            return call
    return None


def test_engine_file_sinks_are_block_buffered(tmp_path: Path) -> None:
    adder: _RecordingSinkAdder = _RecordingSinkAdder()

    session = configure_logging(
        verbosity=2,
        log_directory=tmp_path,
        run_id="run-block-buf",
        structured=True,
        sink_adder=adder,
    )

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

    assert text_call.kwargs.get("buffering") == _FILE_SINK_BUFFER_BYTES, (
        f"ralph.log sink must be passed buffering={_FILE_SINK_BUFFER_BYTES}; got"
        f" {text_call.kwargs.get('buffering')!r}"
    )
    assert structured_call.kwargs.get("buffering") == _FILE_SINK_BUFFER_BYTES, (
        f"ralph.jsonl sink must be passed buffering={_FILE_SINK_BUFFER_BYTES}; got"
        f" {structured_call.kwargs.get('buffering')!r}"
    )

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

    assert structured_call.kwargs.get("serialize") is True, (
        f"ralph.jsonl serialize kwarg changed: {structured_call.kwargs.get('serialize')!r}"
    )
    assert structured_call.kwargs.get("level") == "INFO", (
        f"ralph.jsonl level kwarg changed: {structured_call.kwargs.get('level')!r}"
    )
    assert structured_call.kwargs.get("backtrace") is True, (
        f"ralph.jsonl backtrace kwarg changed: {structured_call.kwargs.get('backtrace')!r}"
    )
    assert structured_call.kwargs.get("diagnose") is False, (
        f"ralph.jsonl diagnose kwarg changed: {structured_call.kwargs.get('diagnose')!r}"
    )
    assert structured_call.kwargs.get("rotation") == "10 MB", (
        f"ralph.jsonl rotation kwarg changed: {structured_call.kwargs.get('rotation')!r}"
    )

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

    assert session.paths.run_directory is not None
    assert session.paths.run_directory.exists()


def test_file_sinks_carry_retention_kwarg(tmp_path: Path) -> None:
    adder = _RecordingSinkAdder()

    configure_logging(
        log_directory=tmp_path,
        run_id="run-retention",
        structured=True,
        sink_adder=adder,
    )

    assert [
        _find_call_for(adder.calls, suffix) is not None for suffix in ("ralph.log", "ralph.jsonl")
    ] == [
        True,
        True,
    ]
    for suffix in ("ralph.log", "ralph.jsonl"):
        call = _find_call_for(adder.calls, suffix)
        assert call is not None
        assert call.kwargs.get("retention") == "14 days"


def test_ralph_log_text_sink_is_buffered_when_structured_disabled(tmp_path: Path) -> None:
    adder: _RecordingSinkAdder = _RecordingSinkAdder()

    session = configure_logging(
        verbosity=3,
        log_directory=tmp_path,
        run_id="run-text-only",
        structured=False,
        sink_adder=adder,
    )

    assert len(adder.calls) == 1, (
        f"expected exactly one sink-adder call (ralph.log only);"
        f" got {len(adder.calls)}: {[call.target for call in adder.calls]}"
    )

    text_call: _RecordedSinkAddition | None = _find_call_for(adder.calls, "ralph.log")
    structured_call: _RecordedSinkAddition | None = _find_call_for(adder.calls, "ralph.jsonl")

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

    assert text_call.kwargs.get("level") == "DEBUG"

    assert session.paths.structured_log_path is None
    assert session.paths.text_log_path is not None
