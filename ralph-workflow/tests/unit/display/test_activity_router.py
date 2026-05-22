from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers import AgentOutputLine
from ralph.display.activity_model import ActivityProvider
from ralph.display.activity_router import ActivityRouter, detect_provider_from_command

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_THREE = 3
EXPECTED_TWO = 2


def _make_router_with_stub_parser(lines_per_call: list[AgentOutputLine]) -> ActivityRouter:
    class _StubParser:
        def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
            return iter(lines_per_call)

    return ActivityRouter(parser_factory=lambda _p: _StubParser())


def test_push_three_lines_produces_three_buffer_entries() -> None:
    single_output = [AgentOutputLine(type="text", content="a line")]
    router = _make_router_with_stub_parser(single_output)
    router.push_raw_line("unit-a", "raw1")
    router.push_raw_line("unit-a", "raw2")
    router.push_raw_line("unit-a", "raw3")
    entries = router.get_buffer("unit-a").snapshot()
    assert len(entries) == EXPECTED_THREE


def test_malformed_line_produces_error_event_no_crash() -> None:
    # Non-JSON lines are now passed to the parser, which handles them as raw content.
    # The router does not crash; malformed lines appear as content entries.
    router = ActivityRouter()
    router.push_raw_line("unit-b", "bad-json")
    entries = router.get_buffer("unit-b").snapshot()
    assert len(entries) == 1
    # The entry should contain the raw content (not an "invalid ndjson" error marker)
    assert "bad-json" in entries[0]


def test_unknown_parser_event_is_rendered_instead_of_dropped() -> None:
    router = _make_router_with_stub_parser(
        [AgentOutputLine(type="mystery_event", content='{"kind":"weird","value":1}')]
    )

    router.push_raw_line("unit-b", '{"kind":"weird","value":1}')

    entries = router.get_buffer("unit-b").snapshot()
    assert len(entries) == 1
    assert '{"kind":"weird","value":1}' in entries[0]


def test_get_buffer_same_unit_id_returns_same_instance() -> None:
    router = ActivityRouter()
    buf1 = router.get_buffer("unit-c")
    buf2 = router.get_buffer("unit-c")
    assert buf1 is buf2


def test_detect_provider_from_command_claude() -> None:
    assert detect_provider_from_command(["claude"]) == ActivityProvider.CLAUDE


def test_detect_provider_from_command_opencode() -> None:
    assert detect_provider_from_command(["opencode"]) == ActivityProvider.OPENCODE


def test_detect_provider_from_command_codex() -> None:
    assert detect_provider_from_command(["codex"]) == ActivityProvider.CODEX


def test_detect_provider_from_command_aider() -> None:
    assert detect_provider_from_command(["aider"]) == ActivityProvider.CODEX


def test_detect_provider_from_command_gemini() -> None:
    assert detect_provider_from_command(["gemini"]) == ActivityProvider.GEMINI


def test_detect_provider_from_command_agy() -> None:
    assert detect_provider_from_command(["agy"]) == ActivityProvider.AGY


def test_detect_provider_from_command_unknown() -> None:
    assert detect_provider_from_command(["unknown-tool"]) == ActivityProvider.GENERIC


def test_detect_provider_from_command_empty() -> None:
    assert detect_provider_from_command([]) == ActivityProvider.GENERIC


def test_parsers_are_not_shared_across_unit_ids() -> None:
    created: list[object] = []

    class _TrackingParser:
        def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
            return iter([])

    def factory(_p: ActivityProvider) -> _TrackingParser:
        parser = _TrackingParser()
        created.append(parser)
        return parser

    router = ActivityRouter(parser_factory=factory)
    router.push_raw_line("unit-x", "{}")
    router.push_raw_line("unit-y", "{}")
    assert len(created) == EXPECTED_TWO
    assert created[0] is not created[1]
