"""Bounded helpers for JSON-envelope ``bytes_out`` / ``bytes_in`` counters.

Compact ``format='summary'`` tools (git_log, git_show, web_search,
visit_url, download_url, read_image, read_media) all emit a JSON
envelope that ends with a ``bytes_out`` field recording the UTF-8
length of the envelope the agent actually sees. Computing the
right value is non-trivial because the field is part of the
payload it describes.

ponytail: previous implementations computed ``bytes_out`` from the
serialized envelope BEFORE adding the ``bytes_out`` field, then
re-serialized the envelope with the field included, producing a
returned text whose real byte length was ``len(serialized) + ~10``
bytes (the digits plus the comma/quotes). Operators reading the
``bytes_out`` counter saw a value 8-12 bytes smaller than the
actual response, which broke byte-budget planning and made the
audit register undercount transcript bytes.

The fix serializes once with a placeholder, then iterates (≤2
steps in practice) until ``bytes_out`` equals the length of the
serialized envelope that includes it. The helper is a pure
function with no side effects, fully black-box testable.

This module is intentionally small and dependency-free so the
audit can import it inside tools that must not pull new runtime
dependencies.
"""

from __future__ import annotations

import json


def finalize_envelope_bytes_out(envelope: dict[str, object]) -> dict[str, object]:
    """Return a copy of ``envelope`` with ``bytes_out`` set to the
    UTF-8 byte length of the final JSON serialization.

    The returned dict is a fresh shallow copy; the input is not
    mutated. Serialization uses the same ``separators=(",", ":")``
    convention as the rest of the MCP tools so the digit count
    matches what callers see in their final text payload.

    Args:
        envelope: dict that *will* contain a ``bytes_out`` key after
            this call. ``bytes_out`` is set to ``0`` as a placeholder
            for the first serialization, then the iteration updates
            it until the length matches the serialized length.

    Returns:
        A new dict with the same keys/values as ``envelope`` plus a
        ``bytes_out`` field whose value equals
        ``len(json.dumps(result, separators=(",", ":")).encode("utf-8"))``.
    """
    working = dict(envelope)
    working["bytes_out"] = 0
    # ponytail: 1-3 iterations fix the self-referential length.
    # Each iteration re-serializes; the loop exits when the stored
    # ``bytes_out`` equals the length of the next serialization.
    for _ in range(4):
        serialized = json.dumps(working, separators=(",", ":"))
        new_value = len(serialized.encode("utf-8"))
        if working.get("bytes_out") == new_value:
            return working
        working["bytes_out"] = new_value
    return working


__all__ = ["finalize_envelope_bytes_out"]
