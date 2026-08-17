"""Transport-level multimodal status contract tests (criterion 16).

The companion to :mod:`test_multimodal_capabilities`: every
:class:`ralph.config.agent_transport.AgentTransport` value must
have a declared, observable multimodal status. The status decides
which transports are covered (round trip graded + perceptible
delivery required), which are excluded with a named reason, and
which carry the negative ``no-MCP`` contract ``GENERIC`` holds.

The contract is closed — three values:

    covered, excluded, no_mcp

A future AgentTransport addition must declare one of these or
registration closes. The test enumerates ``AgentTransport`` and
fails when a member has no declared status, so a new transport
cannot silently inherit "unaccounted".
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from ralph.config.agent_transport import AgentTransport

# ---------------------------------------------------------------------------
# MultimodalStatus enum + canonical table
# ---------------------------------------------------------------------------


class MultimodalStatus(StrEnum):
    """Closed per-transport multimodal status vocabulary."""

    COVERED = "covered"
    EXCLUDED = "excluded"
    NO_MCP = "no_mcp"


# Per-transport explicit multimodal status. ``GENERIC`` carries the
# negative contract (no MCP by design); ``CODEX`` and ``PI`` are
# declared covered at criterion 16 (criterion 5 widens their smoke
# surface). Every non-GENERIC transport is COVERED by criterion 16.
_TRANSPORT_STATUS: dict[AgentTransport, MultimodalStatus] = {
    AgentTransport.CLAUDE: MultimodalStatus.COVERED,
    AgentTransport.CLAUDE_INTERACTIVE: MultimodalStatus.COVERED,
    AgentTransport.CODEX: MultimodalStatus.COVERED,
    AgentTransport.OPENCODE: MultimodalStatus.COVERED,
    AgentTransport.NANOCODER: MultimodalStatus.COVERED,
    AgentTransport.AGY: MultimodalStatus.COVERED,
    AgentTransport.PI: MultimodalStatus.COVERED,
    AgentTransport.CURSOR: MultimodalStatus.COVERED,
    AgentTransport.KIMI: MultimodalStatus.COVERED,
    AgentTransport.GENERIC: MultimodalStatus.NO_MCP,
}


def transport_status(transport: AgentTransport) -> MultimodalStatus:
    """Return the multimodal status for ``transport``.

    Raises ``KeyError`` for an undocumented transport. The
    closed-vocabulary contract ensures callers can switch on the
    returned status without spelling drift.
    """
    return _TRANSPORT_STATUS[transport]


def all_transport_status_pairs() -> tuple[tuple[AgentTransport, MultimodalStatus], ...]:
    """Return all transport/status pairs in deterministic order."""
    return tuple((t, _TRANSPORT_STATUS[t]) for t in sorted(_TRANSPORT_STATUS, key=str))


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def test_every_transport_has_a_declared_multimodal_status() -> None:
    """Each AgentTransport member carries an explicit multimodal status."""
    declared = set(_TRANSPORT_STATUS.keys())
    expected = set(AgentTransport)
    assert declared == expected, (
        f"declared transport set {declared!r} does not equal "
        f"AgentTransport membership {expected!r}; future transport "
        "addition requires an entry in _TRANSPORT_STATUS"
    )


def test_status_enum_has_three_values_and_no_more() -> None:
    """The status enum is closed: covered / excluded / no_mcp only."""
    values = tuple(MultimodalStatus)
    assert values == (
        MultimodalStatus.COVERED,
        MultimodalStatus.EXCLUDED,
        MultimodalStatus.NO_MCP,
    )


def test_no_transport_is_silently_unaccounted() -> None:
    """No transport returns a non-status sentinel; coverage enumeration succeeds."""
    for transport in AgentTransport:
        # Must raise KeyError on a hole, never silently return a
        # "unaccounted" sentinel.
        status = transport_status(transport)
        assert status in MultimodalStatus


def test_generic_carries_negative_no_mcp_contract() -> None:
    """``GENERIC`` is ``NO_MCP`` because it has no MCP transport by design."""
    assert transport_status(AgentTransport.GENERIC) == MultimodalStatus.NO_MCP


def test_all_non_generic_transports_are_covered() -> None:
    """Per criterion 16, every non-``GENERIC`` transport is ``covered`` (none excluded)."""
    non_generic = [t for t in AgentTransport if t is not AgentTransport.GENERIC]
    assert non_generic, "AgentTransport has only GENERIC?"
    for transport in non_generic:
        assert transport_status(transport) == MultimodalStatus.COVERED, (
            f"transport {transport!r} is non-GENERIC but declared "
            f"{transport_status(transport)!r}; criterion 16 covers all "
            "non-GENERIC transports and the EXCLUDED state stays "
            "defined-but-unused in this codebase"
        )


def test_excluded_state_exists_but_is_unused() -> None:
    """The EXCLUDED state is reserved but not currently assigned to any transport.

    Defined-but-unused keeps the tri-state closed so a future
    transport can be deliberately declined without re-opening the
    vocabulary.
    """
    assert MultimodalStatus.EXCLUDED in MultimodalStatus
    excluded_holders = [
        t for t, status in _TRANSPORT_STATUS.items() if status is MultimodalStatus.EXCLUDED
    ]
    assert excluded_holders == [], (
        f"unexpected excluded transport(s): {excluded_holders!r}; "
        "criterion 16 leaves the state defined-but-unused"
    )


def test_transport_status_lookup_is_deterministic() -> None:
    """Equal inputs produce equal outputs (callers may cache the lookup)."""
    for transport in AgentTransport:
        a = transport_status(transport)
        b = transport_status(transport)
        assert a is b


# ---------------------------------------------------------------------------
# OpenCode catalog-driven inline image delivery (criterion 14)
# ---------------------------------------------------------------------------


def _fake_catalog_entry(model_id: str, modalities_input: tuple[str, ...]) -> object:
    """Build a ModelEntry-like object carrying the catalog's modalities.input.

    The capabilities lookup reaches into ``entry.modalities_input`` only,
    so a thin duck-typed stand-in is enough — no need to construct a real
    ``ModelEntry`` (which would force a round-trip through ``model_validate``).
    """

    class _Entry:
        def __init__(self, modalities: tuple[str, ...]) -> None:
            self.modalities_input = modalities

    return _Entry(modalities_input)


def _stub_opencode_catalog(
    monkeypatch: pytest.MonkeyPatch,
    entries_by_id: dict[str, object],
) -> None:
    """Patch ``ralph.api.opencode.get_model_by_id`` for the current test session.

    The patch is keyed on a session-scoped monkeypatch fixture so every
    test that calls ``_stub_opencode_catalog`` in this module shares the
    same catalogue view — a real catalog fetch from a network is not
    used during tests.
    """
    import ralph.api.opencode as opencode_module

    def _fake_lookup(model_id: str) -> object | None:
        return entries_by_id.get(model_id)

    monkeypatch.setattr(opencode_module, "get_model_by_id", _fake_lookup)


@pytest.fixture
def opencode_catalog_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session-scoped monkeypatch helper for the OpenCode catalog lookup.

    Tests that need a catalog-backed model pass ``entries_by_id`` via
    the ``stub_catalog`` parameter to :func:`_stub_opencode_catalog`;
    tests that do not need a catalog still benefit from this fixture
    so the patch is always in place (the lookup returns ``None`` for
    absent ids, which preserves the legacy ``resource_reference_replay``
    fallback).
    """
    import ralph.api.opencode as opencode_module

    def _fake_lookup(model_id: str) -> object | None:
        return None

    monkeypatch.setattr(opencode_module, "get_model_by_id", _fake_lookup)


@pytest.mark.usefixtures("opencode_catalog_stub")
def test_opencode_catalog_image_modality_resolves_to_inline_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENCODE catalog model with ``modalities.input`` containing ``image`` is inline."""
    from ralph.mcp.multimodal._delivery_mode import DeliveryMode
    from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        get_delivery_mode,
    )

    _stub_opencode_catalog(
        monkeypatch,
        {
            "anthropic/claude-sonnet-4-6": _fake_catalog_entry(
                "anthropic/claude-sonnet-4-6",
                ("text", "image", "pdf"),
            ),
        },
    )

    identity = MultimodalModelIdentity(
        provider="opencode", model_id="anthropic/claude-sonnet-4-6"
    )
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)

    assert verdict.delivery == DeliveryMode.INLINE_IMAGE, (
        f"OPENCODE catalog model with 'image' in modalities.input must resolve to "
        f"INLINE_IMAGE, got {verdict.delivery!r}: {verdict.reason}"
    )


@pytest.mark.usefixtures("opencode_catalog_stub")
def test_opencode_catalog_without_image_modality_still_inlines_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image delivery does not depend on an OpenCode catalog entry."""
    from ralph.mcp.multimodal._delivery_mode import DeliveryMode
    from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        get_delivery_mode,
    )

    _stub_opencode_catalog(
        monkeypatch,
        {
            "text-only/model": _fake_catalog_entry(
                "text-only/model",
                ("text",),
            ),
        },
    )

    identity = MultimodalModelIdentity(provider="opencode", model_id="text-only/model")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)

    assert verdict.delivery == DeliveryMode.INLINE_IMAGE


@pytest.mark.usefixtures("opencode_catalog_stub")
def test_opencode_catalog_lookup_miss_still_inlines_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catalog miss does not suppress the standard MCP image block."""
    from ralph.mcp.multimodal._delivery_mode import DeliveryMode
    from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        get_delivery_mode,
    )

    _stub_opencode_catalog(monkeypatch, {})

    identity = MultimodalModelIdentity(provider="opencode", model_id="absent/model")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)

    assert verdict.delivery == DeliveryMode.INLINE_IMAGE

