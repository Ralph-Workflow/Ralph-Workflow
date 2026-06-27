"""Direct unit test for ``_parse_tool_call_from_description`` (R5 contract pin).

This test pins the production helper
``ralph.agents.idle_watchdog._activity_methods._parse_tool_call_from_description``
as a black-box regression test. The helper is the canonical extraction
for the R5 CURRENT TOOL CALL field; both the watchdog surface
(``diagnostic_snapshot``) and the ``WaitingStatusEvent`` surface populate
the same field via this helper so a description like ``"tool_use:Read"``
surfaces as ``"tool_use"`` everywhere an operator reads it.

The test imports the helper directly from its production location — NOT
from a local mirror copy (the existing per-transport test
``test_cross_transport_subagent_visibility.py`` uses a fragile local
copy at line 155 that could drift away from the canonical verb set).
If a future refactor moves the helper, this test fails with a clear
``ImportError`` pointing at the new location.

Pure-function test: no real subprocess, no ``time.sleep``, no
``FakeClock`` needed. The helper has no I/O and is deterministic.
"""

from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog._activity_methods import (
    _KNOWN_TOOL_CALL_VERBS,
    _parse_tool_call_from_description,
)

# ---------------------------------------------------------------------------
# Canonical verb set pin (12 members; see R5 in the product spec).
# ---------------------------------------------------------------------------

_EXPECTED_CANONICAL_VERBS: frozenset[str] = frozenset(
    {
        "tool_use",
        "tool_result",
        "mcp_tool",
        "subagent",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "webfetch",
        "websearch",
    }
)


def test_known_tool_call_verbs_set_is_twelve_members() -> None:
    """``_KNOWN_TOOL_CALL_VERBS`` MUST be exactly the 12 canonical verbs.

    The R5 contract pins the parser's vocabulary to a closed set of 12
    verbs. Any future addition (or removal) of a verb changes the
    R5 contract and MUST be reviewed against the product spec.
    """
    assert isinstance(_KNOWN_TOOL_CALL_VERBS, frozenset), (
        f"_KNOWN_TOOL_CALL_VERBS MUST be a frozenset for immutability;"
        f" got {type(_KNOWN_TOOL_CALL_VERBS).__name__}"
    )
    assert len(_KNOWN_TOOL_CALL_VERBS) == 12, (
        f"_KNOWN_TOOL_CALL_VERBS MUST contain exactly 12 verbs (R5);"
        f" got {len(_KNOWN_TOOL_CALL_VERBS)}: {sorted(_KNOWN_TOOL_CALL_VERBS)}"
    )
    assert _KNOWN_TOOL_CALL_VERBS == _EXPECTED_CANONICAL_VERBS, (
        f"_KNOWN_TOOL_CALL_VERBS mismatch; expected="
        f"{sorted(_EXPECTED_CANONICAL_VERBS)}, got="
        f"{sorted(_KNOWN_TOOL_CALL_VERBS)}"
    )


# ---------------------------------------------------------------------------
# Per-verb parametrized contract: each canonical verb surfaces as the
# ``verb:`` prefix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # 12 canonical verbs with realistic production descriptions.
        ("tool_use:Read", "tool_use"),
        ("tool_result:Bash", "tool_result"),
        ("mcp_tool:mcp__server__tool", "mcp_tool"),
        ("subagent:child-A", "subagent"),
        ("bash:ls -la /tmp", "bash"),
        ("read:foo.py", "read"),
        ("write:bar.py", "write"),
        ("edit:baz.py", "edit"),
        ("glob:**/*.py", "glob"),
        ("grep:TODO", "grep"),
        ("webfetch:https://example.com", "webfetch"),
        ("websearch:ralph workflow watchdog", "websearch"),
    ],
)
def test_parse_returns_canonical_verb_for_known_prefix(
    description: str, expected: str
) -> None:
    """Each canonical verb ``verb:<rest>`` MUST surface as ``verb``.

    The parser splits on the FIRST ``:`` (not ``": "``) because the
    canonical production format from the NDJSON parser layer is
    ``tool_use:<name>`` with no space after the colon (see
    ``ralph/agents/parsers/claude_interactive.py``). The parser returns
    the substring before the first ``:`` when that substring is in the
    canonical verb set, otherwise ``None``.
    """
    result = _parse_tool_call_from_description(description)
    assert result == expected, (
        f"description={description!r}: expected {expected!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Edge cases: None, empty, no-colon, empty-after-colon, unknown verb,
# multi-colon, sanitized description, leading-bracket.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # None description (no subagent observation yet).
        (None, None),
        # Empty string (falsy description).
        ("", None),
        # No colon at all -- the partition on ``:`` returns ``head=desc``,
        # ``sep=''``, so the parser returns ``None``.
        ("no_colon", None),
        # Empty after the colon -- the head IS a known verb so the
        # parser returns the head regardless of the tail.
        ("tool_use:", "tool_use"),
        # Unknown verb prefix -- the head is NOT in the canonical set.
        ("unknown_verb:Read", None),
        # Multi-colon description -- the partition takes only the head
        # before the FIRST colon, so a description like
        # ``tool_use:Read:something`` still surfaces ``tool_use``.
        ("tool_use:Read:something", "tool_use"),
        # Sanitized description: the sanitizer replaces ``/etc/foo`` with
        # ``<redacted>`` so the post-sanitize string is ``tool_use:<redacted>``.
        # The parser only inspects the head, so the redacted tail is
        # irrelevant.
        ("tool_use:<redacted>", "tool_use"),
        # Real sanitized ``/etc/`` path form (mirrors what
        # ``_sanitize_subagent_description`` produces).
        ("tool_use:/etc/<redacted>", "tool_use"),
        # Leading-bracket description -- ``[subagent] progress: phase=1``
        # partitions as ``head="[subagent] progress"`` which is NOT a
        # canonical verb, so the parser returns ``None``. The
        # ``[subagent]`` marker is a parser-layer signal, NOT a
        # tool-call verb; operators see the full line as
        # ``subagent_activity`` but ``current_subagent_tool_call`` is
        # ``None``.
        ("[subagent] progress: phase=1", None),
        # JSON envelope description -- ``{"type": "child_progress"}``
        # partitions as ``head='{"type"'`` which is NOT a canonical verb.
        ('{"type": "child_progress"}', None),
        # JSON envelope with subagent_activity field that happens to
        # look like ``"subagent_activity": "tool_use:Read"`` -- the
        # partition returns ``head='"subagent_activity"'`` which is NOT
        # a canonical verb. Operators see the full string as
        # ``subagent_activity``; the ``current_subagent_tool_call`` field
        # is ``None``.
        ('"subagent_activity": "tool_use:Read"', None),
    ],
)
def test_parse_returns_none_for_edge_cases(
    description: str | None, expected: str | None
) -> None:
    """Edge cases MUST return ``None`` (not raise, not leak partial data)."""
    result = _parse_tool_call_from_description(description)
    assert result == expected, (
        f"description={description!r}: expected {expected!r}, got {result!r}"
    )


def test_parse_does_not_mutate_known_verb_set() -> None:
    """Repeated calls with the same description MUST NOT mutate the canonical set.

    The canonical verb set is a ``frozenset`` (immutable), but this
    test pins that the helper does not introduce side effects that
    could silently change the set between calls (e.g., a future
    refactor that switches to a mutable set).
    """
    snapshot_before = set(_KNOWN_TOOL_CALL_VERBS)
    for _ in range(100):
        _parse_tool_call_from_description("tool_use:Read")
        _parse_tool_call_from_description(None)
        _parse_tool_call_from_description("")
        _parse_tool_call_from_description("unknown_verb:Read")
    snapshot_after = set(_KNOWN_TOOL_CALL_VERBS)
    assert snapshot_before == snapshot_after, (
        f"_KNOWN_TOOL_CALL_VERBS mutated across calls; before="
        f"{snapshot_before}, after={snapshot_after}"
    )
    assert len(snapshot_after) == 12, (
        f"_KNOWN_TOOL_CALL_VERBS size changed across calls; got"
        f" {len(snapshot_after)} members"
    )
