from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.display.raw_log_breaks import detect_raw_log_breaks

if TYPE_CHECKING:
    from pathlib import Path

_WHOLE = b'{"type":"message","text":"ok"}\n'
_CUT = b'{"type":"agent_end","messages":[{"role":"system"\n'


def _log(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "raw.log"
    path.write_bytes(payload)
    return path


def test_breaks_before_the_start_offset_are_not_read(tmp_path: Path) -> None:
    earlier = _WHOLE + _CUT + (b"\x00" * 64) + b"\n"
    path = _log(tmp_path, earlier + _WHOLE * 3)

    breaks = detect_raw_log_breaks(path, transport=AgentTransport.PI, start_offset=len(earlier))

    assert breaks == []


def test_a_break_after_the_start_offset_reports_its_offset_in_the_whole_file(
    tmp_path: Path,
) -> None:
    earlier = _WHOLE * 5
    path = _log(tmp_path, earlier + _WHOLE + _CUT + _WHOLE)

    breaks = detect_raw_log_breaks(path, transport=AgentTransport.PI, start_offset=len(earlier))

    assert [found.offset for found in breaks] == [len(earlier) + len(_WHOLE)]


def test_a_start_offset_inside_a_line_skips_to_the_next_whole_line(tmp_path: Path) -> None:
    path = _log(tmp_path, _WHOLE * 4)

    breaks = detect_raw_log_breaks(path, transport=AgentTransport.PI, start_offset=len(_WHOLE) + 5)

    assert breaks == []
