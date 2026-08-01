"""Black-box tests for ``TextAccumulator.raw_lines`` bound.

wt-024 memory-perf GAP-MEM-04: ``TextAccumulator.raw_lines`` grows
without bound on input that never hits a ``'\n\n'`` paragraph
boundary. The fix forces a flush once ``raw_lines`` exceeds
``_MAX_RAW_LINES`` so pathological input cannot grow the list without
limit.

Normal paragraph-bounded input is unaffected because the forced flush
only triggers above the cap with no paragraph boundary.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.text_accumulator import _MAX_RAW_LINES, TextAccumulator

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine


def test_raw_lines_bounded_on_no_paragraph_boundary() -> None:
    """raw_lines must be bounded by _MAX_RAW_LINES; forced flush emits a line."""
    acc = TextAccumulator()
    emitted: list[AgentOutputLine] = []
    for _ in range(_MAX_RAW_LINES + 100):
        emitted.extend(
            acc.accumulate(text="x", raw="x\n", kind="text", keep_current_when_empty=False)
        )
    assert len(acc.raw_lines) <= _MAX_RAW_LINES, (
        f"raw_lines exceeded cap: len={len(acc.raw_lines)} > {_MAX_RAW_LINES}"
    )
    assert len(emitted) >= 1, "forced flush should have emitted at least one AgentOutputLine"
    assert all(line.type == "text" for line in emitted), "all emitted lines should have type='text'"


def test_interactive_parser_flushes_consecutive_output_deltas_at_cap() -> None:
    """Consecutive structured output cannot wait until end-of-stream to flush."""

    def lines() -> Iterator[str]:
        for index in range(_MAX_RAW_LINES + 1):
            yield json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": f"x{index}"}]},
                }
            )
        raise AssertionError("parser did not flush before requesting another input line")

    emitted = next(ClaudeInteractiveParser().parse(lines()))

    assert emitted.type == "text"
    assert emitted.content.startswith("x0\n")
    assert emitted.content.endswith(f"x{_MAX_RAW_LINES}\n")
