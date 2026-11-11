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
        (
            "an outer array beside a nested envelope",
            {
                "content": [{"type": "text", "text": "ok"}],
                "result": {"content": [dict(_IMAGE_BLOCK)]},
            },
        ),
        ("a tuple of blocks", ({"type": "text", "text": "ok"}, dict(_IMAGE_BLOCK))),
        ("a tuple under content", {"content": (dict(_IMAGE_BLOCK),)}),
        ("a tuple under a nested envelope", {"result": {"content": (dict(_IMAGE_BLOCK),)}}),
    ],
)
def test_no_result_shape_carries_embedded_media_through(label: str, raw: object) -> None:
    normalised = repr(_normalise(raw, session=MockSessionWithManifest("media.read")))

    assert _B64 not in normalised, label
    assert "resource_reference" in normalised, label


def test_text_only_results_are_left_alone() -> None:
    normalised = _normalise(
        [{"type": "text", "text": "hello"}], session=MockSessionWithManifest("media.read")
    )

    assert normalised == [{"type": "text", "text": "hello"}]


def test_a_bare_list_without_a_session_fails_closed() -> None:
    with pytest.raises(UpstreamCallError, match="no active session"):
        _normalise([dict(_IMAGE_BLOCK)], session=None)


def test_a_text_block_that_decodes_to_media_is_not_served_as_a_payload() -> None:
    import json

    from ralph.mcp.server._mcp_server import decode_json_payload_from_content
    from ralph.mcp.upstream.client import carries_upstream_media_blocks

    smuggled = [{"type": "text", "text": json.dumps({"content": [dict(_IMAGE_BLOCK)]})}]
    ordinary = [{"type": "text", "text": json.dumps({"content": [{"type": "text", "text": "ok"}]})}]

    assert decode_json_payload_from_content(smuggled) is None
    assert decode_json_payload_from_content(ordinary) == {
        "content": [{"type": "text", "text": "ok"}]
    }

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

    assert carries_upstream_media_blocks(None) is False

    assert carries_upstream_media_blocks({"type": "image", "data": _B64}) is True
    assert carries_upstream_media_blocks("not a list") is True
    assert carries_upstream_media_blocks((dict(_IMAGE_BLOCK),)) is True


def test_a_content_field_that_is_not_a_list_is_refused() -> None:
    from ralph.mcp.upstream.models import UpstreamCallError

    session = MockSessionWithManifest("media.read")

    for label, raw in (
        ("an object where a block list belongs", {"content": dict(_IMAGE_BLOCK)}),
        ("the same, nested in an envelope", {"result": {"content": dict(_IMAGE_BLOCK)}}),
        ("a string where a block list belongs", {"content": "not blocks"}),
    ):
        with pytest.raises(UpstreamCallError, match="not a list of blocks") as refused:
            _normalise(raw, session=session)
        assert "content" in str(refused.value), label

    normalised = repr(_normalise({"content": [dict(_IMAGE_BLOCK)]}, session=session))
    assert _B64 not in normalised
    assert _normalise({"structuredContent": {"ok": True}}, session=session) == {
        "structuredContent": {"ok": True}
    }
