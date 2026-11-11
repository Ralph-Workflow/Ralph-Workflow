from __future__ import annotations

import re
from pathlib import Path

_SMOKE_PLUMBING_PATH = (
    Path(__file__).resolve().parent.parent / "ralph" / "pipeline" / "plumbing" / "smoke_plumbing.py"
)

_ALLOWED_TRANSPORT_REFERENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"^is_agy = transport is AgentTransport\.AGY$",
        "identification: routes to the AGY-shaped smoke prompt (dispatcher hint text)",
    ),
    (
        r"^if transport is AgentTransport\.OPENCODE$",
        "identification: selects OpenCode's advertised ralph_-prefixed tool naming guidance",
    ),
    (
        r"^if params\.config\.transport != AgentTransport\.NANOCODER or artifact_submitted:$",
        "additive diagnostic: _nanocoder_prompt_submission_error only ever ADDS an error "
        "for nanocoder's own known startup-banner-stall shape; it never skips a check "
        "another transport must pass",
    ),
    (
        r"^if lines or config\.transport != AgentTransport\.OPENCODE:$",
        "additive diagnostic: _opencode_empty_transcript_error only ever ADDS an error for "
        "OpenCode's own known empty-transcript shape",
    ),
    (
        r"^if transport is AgentTransport\.OPENCODE$",
        "identification: adds OpenCode's transport-specific MCP tool naming guidance",
    ),
    (
        r"^if params\.config\.transport == AgentTransport\.AGY:$",
        "additive diagnostic: _agy_upstream_diagnostic only ever ADDS an error for AGY's own "
        "known upstream-failure shape",
    ),
    (
        r"^if transport is AgentTransport\.OPENCODE$",
        "identification: adds OpenCode's documented ralph_ tool-name prefix guidance; it does not "
        "skip or weaken a shared smoke check",
    ),
)


def _read_smoke_plumbing() -> str:
    return _SMOKE_PLUMBING_PATH.read_text(encoding="utf-8")


def test_every_transport_reference_is_identification_or_additive_diagnostic() -> None:
    source = _read_smoke_plumbing()
    compiled = [
        (re.compile(pattern), reason) for pattern, reason in _ALLOWED_TRANSPORT_REFERENCE_PATTERNS
    ]

    offenders: list[str] = []
    for line_no, raw_line in enumerate(source.splitlines(), start=1):
        if not re.search(r"AgentTransport\.[A-Z_]+", raw_line):
            continue
        stripped = raw_line.strip()
        if not any(pattern.match(stripped) for pattern, _reason in compiled):
            offenders.append(f"{line_no}: {stripped!r}")

    assert not offenders, (
        "smoke_plumbing.py contains AgentTransport.<X> reference(s) not in the reviewed "
        "allowlist -- DoD 20 requires every such branch to be either transport "
        "identification or an additive diagnostic, never a check-skipping exemption for "
        "one transport. Add the new reference to _ALLOWED_TRANSPORT_REFERENCE_PATTERNS "
        "ONLY after confirming it does not soften a shared check for one transport:\n"
        + "\n".join(offenders)
    )


def test_no_literal_transport_exclusion_set_anywhere_in_smoke_plumbing() -> None:
    source = _read_smoke_plumbing()
    match = re.search(r"transport\s+(?:not\s+)?in\s+\{\s*\n(\s*AgentTransport\.)", source)
    if match is not None:
        raise AssertionError(
            f"a literal transport-membership exemption set was found in smoke_plumbing.py "
            f"near {match.group(0)!r} -- DoD 20 requires such checks to be expressed as a "
            "general, transport-declared property (e.g. a field on AgentSupport), never a "
            "per-transport enum comparison set."
        )
