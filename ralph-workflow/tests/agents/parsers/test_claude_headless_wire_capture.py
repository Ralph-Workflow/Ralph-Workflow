"""Pin ``ClaudeParser`` behavior against real ``claude -p`` wire captures.

``claude -p --output-format=stream-json --include-partial-messages`` is
Claude Code's headless transport (the ``claude-headless`` builtin agent).
Unlike the synthetic hand-written cases in
``tests/test_claude_parser_noise_suppression.py``, the two fixtures here
are genuine, in-order, single-turn captures taken directly from a live
``claude -p`` invocation (bulky fields -- base64 ``signature`` blobs, the
``system/init`` session-metadata payload, and the long ``hook_response``
output -- are replaced with short placeholders; every event's ``type``/
``subtype``/shape is untouched). Feeding a full in-order session (rather
than cherry-picked lines) matters because the parser is stateful: text and
thinking deltas accumulate per ``(message_id, content_block_index)``, so a
fixture must preserve the real ``message_start`` -> ``content_block_start``
-> deltas -> ``content_block_stop`` -> ``message_stop`` pairing to exercise
that machinery honestly.

  - ``claude_headless_success_turn.jsonl`` -- a turn that runs a Bash
    command successfully and reports the output.
  - ``claude_headless_tool_error_turn.jsonl`` -- a turn that runs a
    failing Bash command (``cat`` on a nonexistent file) and reports the
    failure; captures a real ``is_error=true`` ``tool_result``.

This test asserts:

  (a) the parser runs over each full real session without raising;
  (b) the noise ``system`` subtypes present in the fixtures (``init``,
      ``hook_started``, ``hook_response``, ``thinking_tokens``) never
      reach the output -- neither as their own line nor smuggled in via
      any other line's metadata subtype;
  (c) no output line has type ``system``, ``user``, ``rate_limit_event``,
      or ``message_delta`` with EMPTY content -- the exact shape of the
      original bug (a bare, content-less line flooding operator-visible
      output);
  (d) the real ``tool_result`` success content is captured verbatim;
  (e) the real ``is_error=true`` tool_result surfaces as ``type="error"``
      (not ``type="tool_result"``) with the real error text, so a tool
      failure is a distinguishable break signal rather than reading as a
      routine result;
  (f) the real final assistant text (``"Done."`` / the failure summary)
      is captured;
  (g) the per-turn ``system/status`` event (a real, non-noise subtype)
      still surfaces, proving the noise filter is a targeted allowlist
      and not a blanket ``type="system"`` suppression.

When Claude Code's headless wire format changes in a way that breaks
these fixtures, both the parser and the committed fixtures must be
updated in the same diff so this test never silently drifts from the
live wire format.
"""

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
    """Guard the guard: fail loudly if a fixture edit drops the noise cases."""
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
    """None of the known-noise subtypes leak through as their own line or
    hide inside another line's metadata."""
    for fixture in (_SUCCESS_FIXTURE, _TOOL_ERROR_FIXTURE):
        parser = ClaudeParser()
        results = list(parser.parse(iter(_load_fixture_lines(fixture))))
        for line in results:
            subtype = line.metadata.get("subtype") if line.metadata else None
            assert subtype not in _CLAUDE_NOISE_SYSTEM_SUBTYPES, (
                f"noise subtype {subtype!r} leaked through as {line!r} in {fixture.name}"
            )


def test_no_noise_bearing_type_has_empty_content() -> None:
    """Regression guard for the original bug: a bare ``type="system"``
    (or user/rate_limit_event/message_delta) line with no content,
    flooding operator-visible output."""
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
    # And it must NOT also appear as a (misclassified) successful tool_result.
    tool_results = [r for r in results if r.type == "tool_result"]
    assert not any("No such file or directory" in r.content for r in tool_results), tool_results


def test_real_final_assistant_text_is_captured() -> None:
    parser = ClaudeParser()
    success_results = list(parser.parse(iter(_load_fixture_lines(_SUCCESS_FIXTURE))))
    texts = [r.content for r in success_results if r.type == "text"]
    assert any("Done" in t for t in texts), texts

    parser = ClaudeParser()
    error_results = list(parser.parse(iter(_load_fixture_lines(_TOOL_ERROR_FIXTURE))))
    error_texts = [r.content for r in error_results if r.type == "text"]
    assert any("failed" in t.lower() or "doesn't exist" in t for t in error_texts), error_texts


def test_real_system_status_event_still_surfaces() -> None:
    """The noise filter is a targeted allowlist, not a blanket type="system"
    suppression: the real per-turn ``status`` subtype must still surface."""
    parser = ClaudeParser()
    results = list(parser.parse(iter(_load_fixture_lines(_SUCCESS_FIXTURE))))
    system_lines = [r for r in results if r.type == "system"]
    assert system_lines, "expected the real system/status event to surface"
    assert all(r.content for r in system_lines)


def test_fixtures_are_valid_ndjson() -> None:
    """Sanity-check the committed fixtures themselves: every non-blank line
    must be a single valid JSON object (guards against a bad hand-edit)."""
    for fixture in (_SUCCESS_FIXTURE, _TOOL_ERROR_FIXTURE):
        for line in _load_fixture_lines(fixture):
            if not line.strip():
                continue
            parsed: object = json.loads(line)
            assert isinstance(parsed, dict), f"non-object JSON line in {fixture.name}: {line!r}"
