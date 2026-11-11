from __future__ import annotations

import json
from pathlib import Path

from ralph.agents.parsers.claude import (
    _CLAUDE_NOISE_SYSTEM_SUBTYPES,
    ClaudeParser,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SUCCESS_FIXTURE = _FIXTURES_DIR / "claude_headless_success_turn.jsonl"
_TOOL_ERROR_FIXTURE = _FIXTURES_DIR / "claude_headless_tool_error_turn.jsonl"

_NOISE_BEARING_TYPES = frozenset({"system", "user", "rate_limit_event", "message_delta"})


def _load_fixture_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_fixtures_actually_contain_the_noise_subtypes_they_claim_to() -> None:
    raw_text = _SUCCESS_FIXTURE.read_text(encoding="utf-8")
    for subtype in _CLAUDE_NOISE_SYSTEM_SUBTYPES:
        assert f'"{subtype}"' in raw_text, f"fixture no longer covers noise subtype {subtype!r}"


def test_success_turn_parses_without_raising() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(iter(_load_fixture_lines(_SUCCESS_FIXTURE))))
    assert results, "expected at least one classified line from a real session"


def test_tool_error_turn_parses_without_raising() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(iter(_load_fixture_lines(_TOOL_ERROR_FIXTURE))))
    assert results, "expected at least one classified line from a real session"


def test_noise_system_subtypes_never_reach_output() -> None:
    for fixture in (_SUCCESS_FIXTURE, _TOOL_ERROR_FIXTURE):
        parser = ClaudeParser()
        results = list(parser.parse(iter(_load_fixture_lines(fixture))))
        for line in results:
            subtype = line.metadata.get("subtype") if line.metadata else None
            assert subtype not in _CLAUDE_NOISE_SYSTEM_SUBTYPES, (
                f"noise subtype {subtype!r} leaked through as {line!r} in {fixture.name}"
            )


def test_no_noise_bearing_type_has_empty_content() -> None:
    for fixture in (_SUCCESS_FIXTURE, _TOOL_ERROR_FIXTURE):
        parser = ClaudeParser()
        results = list(parser.parse(iter(_load_fixture_lines(fixture))))
        for line in results:
            if line.type in _NOISE_BEARING_TYPES:
                assert line.content, (
                    f"empty-content {line.type!r} line leaked through in {fixture.name}: {line!r}"
                )


def test_real_tool_result_success_content_is_captured() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(iter(_load_fixture_lines(_SUCCESS_FIXTURE))))
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any(r.content == "test-msg-delta" for r in tool_results), tool_results


def test_real_tool_result_failure_surfaces_as_error_not_tool_result() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(iter(_load_fixture_lines(_TOOL_ERROR_FIXTURE))))
    errors = [r for r in results if r.type == "error"]
    assert any("No such file or directory" in r.content for r in errors), errors
    tool_results = [r for r in results if r.type == "tool_result"]
    assert not any("No such file or directory" in r.content for r in tool_results), tool_results


def test_claude_headless_regression_each_streamed_content_block_emits_once() -> None:
    success_results = list(ClaudeParser().parse(iter(_load_fixture_lines(_SUCCESS_FIXTURE))))
    assert [r.content for r in success_results if r.type == "text"].count("Done.") == 1
    assert [r.content for r in success_results if r.type == "tool_use"].count("Bash") == 1
    assert len([r for r in success_results if r.type == "thinking"]) == 7

    error_results = list(ClaudeParser().parse(iter(_load_fixture_lines(_TOOL_ERROR_FIXTURE))))
    failure_summary = (
        "It failed. The file `/nonexistent-file-xyz-123.txt` doesn't exist, so "
        '`cat` returned exit code 1 with the error: "No such file or directory".'
    )
    assert [r.content for r in error_results if r.type == "text"].count(failure_summary) == 1
    assert [r.content for r in error_results if r.type == "tool_use"].count("Bash") == 1
    assert len([r for r in error_results if r.type == "thinking"]) == 4


def test_real_system_status_event_still_surfaces() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(iter(_load_fixture_lines(_SUCCESS_FIXTURE))))
    system_lines = [r for r in results if r.type == "system"]
    assert system_lines, "expected the real system/status event to surface"
    assert all(r.content for r in system_lines)


def test_fixtures_are_valid_ndjson() -> None:
    for fixture in (_SUCCESS_FIXTURE, _TOOL_ERROR_FIXTURE):
        for line in _load_fixture_lines(fixture):
            if not line.strip():
                continue
            parsed: object = json.loads(line)
            assert isinstance(parsed, dict), f"non-object JSON line in {fixture.name}: {line!r}"
