"""Regression test: no AGY-only exemption branches in ``smoke_plumbing.py``.

Brief ``.agent/PRODUCT_CRITERIA.md`` F7 / DoD 17 / DoD 19 -- the four
AGY-only branches the brief names by line number are all gone, and the
gate is now rewritten so AGY clears the same bar as every other
transport. A grep for ``AgentTransport.AGY`` in ``smoke_plumbing.py``
MUST return only transport *identification* and additive diagnostics,
never a branch that skips, relaxes, or substitutes for a check another
transport must pass.

The four branches that were deleted (do not reintroduce them):

1. ``_meaningful_output_error``: two ``if config.transport ==
   AgentTransport.AGY and meaningful_output: return None`` early returns.
2. ``_tool_activity_seen_for_errors``: ``params.config.transport ==
   AgentTransport.AGY and params.output_file.exists()`` fallback.
3. ``_detect_smoke_errors``: ``AgentTransport.AGY`` in the session-ID
   exemption set alongside ``NANOCODER``.
4. ``_run_smoke_agent``: the ``host_synthesized_sentinel`` branch that
   called ``_write_completion_sentinel`` on AGY's behalf.

The negative assertions below encode each of those deletions as a
search-able invariant so the lane cannot silently reopen.
"""

from __future__ import annotations

import re
from pathlib import Path

_SMOKE_PLUMBING_PATH = (
    Path(__file__).resolve().parent.parent
    / "ralph"
    / "pipeline"
    / "plumbing"
    / "smoke_plumbing.py"
)


def _read_smoke_plumbing() -> str:
    return _SMOKE_PLUMBING_PATH.read_text(encoding="utf-8")


def test_agy_tool_activity_fallback_is_gone() -> None:
    """The AGY workspace-file-as-tool-activity fallback was removed in S-1.

    Pre-fix branch (smoke_plumbing.py:1163) read:

        return params.config.transport == AgentTransport.AGY and params.output_file.exists()

    Tool activity for AGY now comes from parser-classified tool events
    like every other transport (F7/DoD 20).
    """
    source = _read_smoke_plumbing()
    assert (
        "params.config.transport == AgentTransport.AGY and params.output_file.exists()"
        not in source
    ), (
        "AGY-specific workspace-output-file tool-activity fallback "
        "reintroduced in smoke_plumbing.py -- F7/DoD 20 forbids a tool-"
        "activity source that exists for AGY alone."
    )


def test_agy_meaningful_output_exemption_is_gone() -> None:
    """The two AGY-only early-return short-circuits in ``_meaningful_output_error`` are gone.

    Pre-fix branches (smoke_plumbing.py:1000, :1008) read:

        if config.transport == AgentTransport.AGY and meaningful_output:
            return None

    AGY now falls through to the same three-tier meaningful-output check
    as every other transport.
    """
    source = _read_smoke_plumbing()
    matches = re.findall(
        r"if config\.transport == AgentTransport\.AGY and meaningful_output",
        source,
    )
    assert matches == [], (
        f"AGY-only meaningful-output exemption reintroduced "
        f"({len(matches)} occurrences); AGY must fall through to the "
        f"shared three-tier check."
    )


def test_agy_session_id_exemption_is_gone() -> None:
    """``AgentTransport.AGY`` must not appear in the session-ID exemption set.

    Pre-fix branch (smoke_plumbing.py:1190) included AGY alongside
    ``NANOCODER`` in the set of transports that do not need a session ID.
    Post-F7, only ``NANOCODER`` retains the exemption (out of this brief's
    scope; the brief only restricts what F7 names for AGY).
    """
    source = _read_smoke_plumbing()
    match = re.search(
        r"if session_id is None and params\.config\.transport not in \{\s*\n"
        r"(\s+AgentTransport\.[A-Z_]+,?\s*\n)+\s*\}\:",
        source,
    )
    assert match is not None, (
        "Could not locate the session-ID exemption set in smoke_plumbing.py; "
        "this regression must continue to track the exact exempt set."
    )
    exemption_block = match.group(0)
    assert "AgentTransport.AGY" not in exemption_block, (
        "AgentTransport.AGY reintroduced into the session-ID exemption "
        "set -- every transport must surface 'session ID was not observed' "
        "when the session ID is missing."
    )


def test_agy_host_synthesized_sentinel_branch_is_gone() -> None:
    """The host must never call ``_write_completion_sentinel`` on AGY's behalf.

    Pre-fix branch (smoke_plumbing.py:1441-1456) included:

        if (params.config.transport == AgentTransport.AGY and ...):
            _write_completion_sentinel(...)
            host_synthesized_sentinel = True

    Post-F7/DoD 19, the host writes completion evidence for no transport.
    """
    source = _read_smoke_plumbing()
    assert "_write_completion_sentinel" not in source, (
        "smoke_plumbing.py imports/calls _write_completion_sentinel -- the "
        "host now writes completion evidence for no transport (F7/DoD 19). "
        "If a future change re-introduces host-side sentinel writing, this "
        "test fails and forces a deliberate review."
    )


def test_agy_branches_in_smoke_plumbing_are_identification_or_diagnostic_only() -> None:
    """Every remaining ``AgentTransport.AGY`` reference is identification or additive diagnostic.

    Per F7: ``grep`` for ``AgentTransport.AGY`` in ``smoke_plumbing.py``
    returns only transport *identification* (e.g. building the AGY-shaped
    prompt) and additive diagnostics -- never a branch that skips,
    relaxes, or substitutes for a check another transport must pass.
    """
    source = _read_smoke_plumbing()
    matches: list[tuple[int, str]] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        if "AgentTransport.AGY" in line:
            matches.append((line_no, line.strip()))

    assert matches, (
        "Expected at least one AGY identification reference (e.g. the "
        "is_agy prompt branch); if every reference was removed, the "
        "AGY-shaped prompt path is broken."
    )

    for line_no, line in matches:
        # Additive diagnostic in ``_detect_smoke_errors`` -- reports an
        # AGY-specific upstream diagnostic; not skipping/relaxing a check.
        is_additive_diagnostic = bool(
            re.match(
                r"^if params\.config\.transport == AgentTransport\.AGY\s*:\s*$",
                line,
            )
        )
        # Transport identification in ``_build_smoke_prompt`` -- renders
        # AGY-shaped submission instructions; not skipping/relaxing a check.
        is_transport_identification = bool(
            re.match(
                r"^is_agy = transport is AgentTransport\.AGY\s*$",
                line,
            )
        )
        assert is_additive_diagnostic or is_transport_identification, (
            f"smoke_plumbing.py:{line_no} uses AgentTransport.AGY in a "
            f"branch that is neither transport identification nor an "
            f"additive diagnostic: {line!r}"
        )
