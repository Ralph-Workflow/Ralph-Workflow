from __future__ import annotations

import re
from pathlib import Path

_SMOKE_PLUMBING_PATH = (
    Path(__file__).resolve().parent.parent / "ralph" / "pipeline" / "plumbing" / "smoke_plumbing.py"
)


def _read_smoke_plumbing() -> str:
    return _SMOKE_PLUMBING_PATH.read_text(encoding="utf-8")


def test_agy_tool_activity_fallback_is_gone() -> None:
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
    source = _read_smoke_plumbing()
    assert re.search(r"AgentTransport\.[A-Z_]+\s+not in\s+\{", source) is None, (
        "a literal 'transport not in {...}' exemption set was reintroduced "
        "in smoke_plumbing.py -- DoD 20 requires exemptions to be expressed "
        "as a general, transport-declared property, never a per-transport "
        "comparison."
    )

    from ralph.agents.builtin import builtin_supports
    from ralph.config.enums import AgentTransport

    agy_support = next(
        support for support in builtin_supports() if support.transport == AgentTransport.AGY
    )
    assert agy_support.session_identifier_observable is True, (
        "AGY's registered AgentSupport must keep the default "
        "session_identifier_observable=True -- AGY is not exempted from "
        "the smoke gate's session-ID check."
    )


def test_agy_host_synthesized_sentinel_branch_is_gone() -> None:
    source = _read_smoke_plumbing()
    assert "_write_completion_sentinel" not in source, (
        "smoke_plumbing.py imports/calls _write_completion_sentinel -- the "
        "host now writes completion evidence for no transport (F7/DoD 19). "
        "If a future change re-introduces host-side sentinel writing, this "
        "test fails and forces a deliberate review."
    )


def test_agy_branches_in_smoke_plumbing_are_identification_or_diagnostic_only() -> None:
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
        is_additive_diagnostic = bool(
            re.match(
                r"^if params\.config\.transport == AgentTransport\.AGY\s*:\s*$",
                line,
            )
        )
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
