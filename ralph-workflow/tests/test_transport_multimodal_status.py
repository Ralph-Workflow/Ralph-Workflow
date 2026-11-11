from __future__ import annotations

from enum import StrEnum

import pytest

from ralph.config.agent_transport import AgentTransport


class MultimodalStatus(StrEnum):
    COVERED = "covered"
    EXCLUDED = "excluded"
    NO_MCP = "no_mcp"


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
    return _TRANSPORT_STATUS[transport]


def all_transport_status_pairs() -> tuple[tuple[AgentTransport, MultimodalStatus], ...]:
    return tuple((t, _TRANSPORT_STATUS[t]) for t in sorted(_TRANSPORT_STATUS, key=str))


def test_every_transport_has_a_declared_multimodal_status() -> None:
    declared = set(_TRANSPORT_STATUS.keys())
    expected = set(AgentTransport)
    assert declared == expected, (
        f"declared transport set {declared!r} does not equal "
        f"AgentTransport membership {expected!r}; future transport "
        "addition requires an entry in _TRANSPORT_STATUS"
    )


def test_status_enum_has_three_values_and_no_more() -> None:
    values = tuple(MultimodalStatus)
    assert values == (
        MultimodalStatus.COVERED,
        MultimodalStatus.EXCLUDED,
        MultimodalStatus.NO_MCP,
    )


def test_no_transport_is_silently_unaccounted() -> None:
    for transport in AgentTransport:
        status = transport_status(transport)
        assert status in MultimodalStatus


def test_generic_carries_negative_no_mcp_contract() -> None:
    assert transport_status(AgentTransport.GENERIC) == MultimodalStatus.NO_MCP


def test_all_non_generic_transports_are_covered() -> None:
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
    assert MultimodalStatus.EXCLUDED in MultimodalStatus
    excluded_holders = [
        t for t, status in _TRANSPORT_STATUS.items() if status is MultimodalStatus.EXCLUDED
    ]
    assert excluded_holders == [], (
        f"unexpected excluded transport(s): {excluded_holders!r}; "
        "criterion 16 leaves the state defined-but-unused"
    )


def test_transport_status_lookup_is_deterministic() -> None:
    for transport in AgentTransport:
        a = transport_status(transport)
        b = transport_status(transport)
        assert a is b


def _fake_catalog_entry(model_id: str, modalities_input: tuple[str, ...]) -> object:

    class _Entry:
        def __init__(self, modalities: tuple[str, ...]) -> None:
            self.modalities_input = modalities

    return _Entry(modalities_input)


def _stub_opencode_catalog(
    monkeypatch: pytest.MonkeyPatch,
    entries_by_id: dict[str, object],
) -> None:
    import ralph.api.opencode as opencode_module

    def _fake_lookup(model_id: str) -> object | None:
        return entries_by_id.get(model_id)

    monkeypatch.setattr(opencode_module, "get_model_by_id", _fake_lookup)


@pytest.fixture
def opencode_catalog_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    import ralph.api.opencode as opencode_module

    def _fake_lookup(model_id: str) -> object | None:
        return None

    monkeypatch.setattr(opencode_module, "get_model_by_id", _fake_lookup)


@pytest.mark.usefixtures("opencode_catalog_stub")
def test_opencode_catalog_image_modality_resolves_to_inline_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    identity = MultimodalModelIdentity(provider="opencode", model_id="anthropic/claude-sonnet-4-6")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)

    assert verdict.delivery == DeliveryMode.INLINE_IMAGE, (
        f"OPENCODE catalog model with 'image' in modalities.input must resolve to "
        f"INLINE_IMAGE, got {verdict.delivery!r}: {verdict.reason}"
    )


@pytest.mark.usefixtures("opencode_catalog_stub")
def test_opencode_catalog_without_image_modality_still_inlines_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
