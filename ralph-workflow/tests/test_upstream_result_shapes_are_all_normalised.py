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
