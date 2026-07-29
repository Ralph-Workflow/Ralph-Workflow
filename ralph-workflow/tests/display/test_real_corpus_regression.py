"""Production-path regression replay for the repaired Pi record corpus."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from ralph.agents.parsers import CodexParser, PiParser
from ralph.display.activity_provider import ActivityProvider
from tests.display.test_raw_record_regression import _drive_fixture_through_production
from tests.display.test_universality_replay import _replay


def test_real_corpus_regression_replays_repaired_shape(tmp_path: Path) -> None:
    """S-2: a defect-shaped Pi slice stays repaired on both surfaces."""
    rendered, live = _drive_fixture_through_production(
        "pi_real_corpus_defects.jsonl", tmp_path, unit_id="pi"
    )
    duplicate = "Command: cd /Volumes/Crucial\\ X9/ext-Projects/Ralph-Workflow/wt-028-display"
    assert rendered.count(duplicate) == live.count(duplicate) == 1
    for surface in (rendered, live):
        assert "[??:??:??]" not in surface
        assert "role=progress" not in surface
        assert "thinking:" not in surface
        assert "⚠ WARN" not in surface
    assert "truncated, 1 line" in rendered
    assert " B, see .agent/raw/pi.log" in rendered


def test_codex_tool_call_regression_same_input_records_remain_distinct(tmp_path: Path) -> None:
    """S-4: rendered records retain each distinct same-input Codex call."""
    wire = [
        '{"type":"item.started","item":{"id":"call-1","type":"mcp_tool_call","tool":"search_files","arguments":{"path":"ralph"}}}',
        '{"type":"item.started","item":{"id":"call-2","type":"mcp_tool_call","tool":"search_files","arguments":{"path":"ralph"}}}',
    ]
    rendered, _live, _ = _replay(
        "codex",
        ActivityProvider.CODEX,
        CodexParser,
        tmp_path,
        wire_lines=wire,
    )

    lines = [line for line in rendered.splitlines() if "role=tool_call" in line]
    assert len(lines) == 2
    assert {"call_id=call-1", "call_id=call-2"} <= set(" ".join(lines).split())


def test_pi_toolcall_triplication_replay_emits_one_call_and_result(tmp_path: Path) -> None:
    """DA-001/DA-002: existing normalized aliases share one presentation entry."""
    rendered, live = _drive_fixture_through_production(
        "pi_toolcall_triplication.jsonl", tmp_path, unit_id="pi"
    )
    assert rendered.count("role=tool_call") == 1
    assert rendered.count("payload from tool") == live.count("payload from tool") == 1


def test_pi_tool_result_flood_replay_collapses_identical_burst_on_both_surfaces(
    tmp_path: Path,
) -> None:
    """S-2: repeated Pi terminal results become one accounted-for presentation."""
    rendered, live, _ = _replay(
        "pi_tool_result_flood", ActivityProvider.PI, PiParser, tmp_path
    )
    assert rendered.count("role=tool_result") == 2
    for surface in (rendered, live):
        collapsed = " ".join(surface.split())
        assert collapsed.count("4 identical results") == 1
        assert "92 B" in surface
        assert "destination src/flood.py" in surface
        assert "[tool-result]" not in surface
        assert "(running...)" not in surface
        assert "severity=info severity=info" not in surface


def test_pi_toolcall_alias_and_idless_echo_replay_deduplicates_both_surfaces(
    tmp_path: Path,
) -> None:
    """DA-001/DA-002: parser-native Pi aliases and result echoes render once."""
    rendered, live, _ = _replay(
        "pi_toolcall_alias_and_echo",
        ActivityProvider.PI,
        PiParser,
        tmp_path,
    )
    record_lines = [line for line in rendered.splitlines() if line.strip()]
    tool_call_lines = [line for line in record_lines if "role=tool_call" in line]
    assert len(tool_call_lines) == 2
    assert all(any(path in line for line in tool_call_lines) for path in ("target.py", "other.py"))
    assert len(set(tool_call_lines)) == len(tool_call_lines)
    assert "severity=error" in rendered
    for surface in (rendered, live):
        lines = [line for line in surface.splitlines() if line.strip()]
        assert sum("Destination target.py already exists" in line for line in lines) == 1
        assert not any(
            "role=agent_text" in line and "Destination target.py already exists" in line
            for line in lines
        )
        assert all(left != right for left, right in pairwise(lines))
