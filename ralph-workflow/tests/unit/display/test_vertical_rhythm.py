from __future__ import annotations

import re
from pathlib import Path

import pytest

_PARALLEL_DISPLAY_PATH = Path(__file__).parents[3] / "ralph" / "display" / "parallel_display.py"

_PANEL_CALL_RE = re.compile(r"^\s*(?:panel = )?Panel\(\s*$", re.MULTILINE)

_DOCUMENTED_PANEL_EXCEPTION_COUNT = 3


@pytest.mark.criteria("E-7")
def test_parallel_display_panel_usage_stays_at_the_documented_exception_count() -> None:
    source = _PARALLEL_DISPLAY_PATH.read_text(encoding="utf-8")
    call_sites = _PANEL_CALL_RE.findall(source)
    assert len(call_sites) == _DOCUMENTED_PANEL_EXCEPTION_COUNT, (
        f"expected exactly {_DOCUMENTED_PANEL_EXCEPTION_COUNT} documented Panel( call sites "
        f"in {_PARALLEL_DISPLAY_PATH.name}, found {len(call_sites)} -- a new Panel( call must "
        "either be justified and added to the documented exception count in "
        "test_vertical_rhythm.py, or converted to Console.rule/indentation per E-7"
    )
