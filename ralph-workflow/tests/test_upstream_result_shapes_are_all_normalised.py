"""No upstream result shape may carry raw media bytes to the agent.

An upstream MCP server's result is normalised so image/audio/pdf blocks
become replay handles: a restricted CLI cannot carry the bytes, and
handing them over kills its turn. That normalisation ran only on a
``dict``, and the server layer separately unwraps a nested
``{"result": {...}}`` AFTER it -- so two shapes reached the agent
verbatim, both delivering a raw base64 ``image`` block to a codex
caller. That is the exact wire shape of the incident this contract
exists to prevent, arriving by the one route that skipped the contract.

An upstream server has to be non-conforming to produce them, which is
precisely the case a guard is for.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ralph.mcp.upstream.models import UpstreamCallError
from ralph.mcp.upstream.registry import UpstreamRegistry
from tests.mock_session_with_manifest import MockSessionWithManifest

_B64 = "iVBORw0KGgoAAAANSUhEUg=="
_IMAGE_BLOCK = {"type": "image", "data": _B64, "mimeType": "image/png"}


def _normalise(raw: object, *, session: object | None = None) -> object:
    registry = UpstreamRegistry.__new__(UpstreamRegistry)
    proxied = SimpleNamespace(server_name="srv", tool=SimpleNamespace(name="t"))
    return registry._normalized_result(raw, proxied, session, None)


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("a bare list of blocks", [dict(_IMAGE_BLOCK)]),
        ("text beside an image", [{"type": "text", "text": "x"}, dict(_IMAGE_BLOCK)]),
        ("a nested result envelope", {"result": {"content": [dict(_IMAGE_BLOCK)]}}),
        ("the spec-shaped result", {"content": [dict(_IMAGE_BLOCK)]}),
        # The shape that survived the first fix: an outer content array
        # BESIDE a nested envelope. The registry declined to normalise
        # the nested one whenever an outer ``content`` existed, while
        # the server unwraps ``result`` UNCONDITIONALLY and discards the
        # outer -- so the two rules were exact opposites and the payload
        # the registry skipped was the payload the server served.
        (
            "an outer array beside a nested envelope",
            {
                "content": [{"type": "text", "text": "ok"}],
                "result": {"content": [dict(_IMAGE_BLOCK)]},
            },
        ),
        # The server layer accepts a tuple wherever it accepts a list.
        ("a tuple of blocks", ({"type": "text", "text": "ok"}, dict(_IMAGE_BLOCK))),
        # A tuple UNDER content: the top-level tuple was fixed one round
        # earlier while this one still took the dict branch, where the
        # content extractor required a list and found nothing to do.
        ("a tuple under content", {"content": (dict(_IMAGE_BLOCK),)}),
        ("a tuple under a nested envelope", {"result": {"content": (dict(_IMAGE_BLOCK),)}}),
    ],
)
def test_no_result_shape_carries_embedded_media_through(label: str, raw: object) -> None:
    """Whatever the envelope, the bytes become a replay handle."""
    normalised = repr(_normalise(raw, session=MockSessionWithManifest("media.read")))

    assert _B64 not in normalised, label
    assert "resource_reference" in normalised, label


def test_text_only_results_are_left_alone() -> None:
    """Not vacuous: normalisation rewrites media, not everything."""
    normalised = _normalise(
        [{"type": "text", "text": "hello"}], session=MockSessionWithManifest("media.read")
    )

    assert normalised == [{"type": "text", "text": "hello"}]


def test_a_bare_list_without_a_session_fails_closed() -> None:
    """Embedded media with nowhere to store it is refused, not passed on.

    This is the same contract the spec-shaped path already honoured; the
    point is that the bare-list shape now reaches it at all.
    """
    with pytest.raises(UpstreamCallError, match="no active session"):
        _normalise([dict(_IMAGE_BLOCK)], session=None)


def test_a_text_block_that_decodes_to_media_is_not_served_as_a_payload() -> None:
    """The JSON-in-text decoder must not re-enter media past the contract.

    ``decode_json_payload_from_content`` replaces the whole tool payload
    with JSON decoded out of a TEXT block -- after the upstream contract
    has already inspected that text block and found nothing to
    normalise. Media smuggled inside the text therefore reached the
    agent having never been through the contract at all.
    """
    import json

    from ralph.mcp.server._mcp_server import decode_json_payload_from_content
    from ralph.mcp.upstream.client import carries_upstream_media_blocks

    smuggled = [{"type": "text", "text": json.dumps({"content": [dict(_IMAGE_BLOCK)]})}]
    ordinary = [
        {"type": "text", "text": json.dumps({"content": [{"type": "text", "text": "ok"}]})}
    ]

    assert decode_json_payload_from_content(smuggled) is None
    # Not vacuous: an ordinary JSON-in-text payload still decodes.
    assert decode_json_payload_from_content(ordinary) == {
        "content": [{"type": "text", "text": "ok"}]
    }

    # FAIL-CLOSED, matching the contract it defends: only the two block
    # types that pass through unchanged are safe. An exact-match
    # allowlist of the five media NAMES was narrower than the contract,
    # so a standard MCP EmbeddedResource carrying base64 image bytes --
    # or a block typed "Image" rather than "image" -- was waved through
    # as "not media" and served to a codex caller.
    assert carries_upstream_media_blocks([{"type": "text", "text": "ok"}]) is False
    assert carries_upstream_media_blocks([{"type": "resource_reference", "uri": "x"}]) is False

    refused = (
        [dict(_IMAGE_BLOCK)],
        [{"type": "resource", "resource": {"mimeType": "image/png", "blob": _B64}}],
        [{"type": "Image", "data": _B64}],
        [{"type": "whatever"}],
        [{"data": _B64}],
        [["not", "a", "mapping"]],
        [None, 5, "x"],
    )
    for content in refused:
        assert carries_upstream_media_blocks(content) is True, content
    for media_type in ("image", "audio", "video", "pdf", "document"):
        assert carries_upstream_media_blocks([{"type": media_type}]) is True, media_type

    # A missing ``content`` is nothing to serve and nothing to refuse.
    assert carries_upstream_media_blocks(None) is False

    # A ``content`` that is not a block SEQUENCE is non-conforming, and
    # the normaliser leaves it untouched for that reason -- so it
    # reaches the agent with its bytes intact. This test previously
    # asserted the opposite ("there is nothing there to serve"), which
    # is how ``{"content": {"type": "image", "data": ...}}`` crossed the
    # boundary un-normalised while the guard was called fail-closed.
    assert carries_upstream_media_blocks({"type": "image", "data": _B64}) is True
    assert carries_upstream_media_blocks("not a list") is True
    # A tuple is a block sequence: the server serialises one like a list.
    assert carries_upstream_media_blocks((dict(_IMAGE_BLOCK),)) is True
