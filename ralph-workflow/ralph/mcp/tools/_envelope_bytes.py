from __future__ import annotations

import json


def finalize_envelope_bytes_out(envelope: dict[str, object]) -> dict[str, object]:
    working = dict(envelope)
    working["bytes_out"] = 0
    for _ in range(4):
        serialized = json.dumps(working, separators=(",", ":"))
        new_value = len(serialized.encode("utf-8"))
        if working.get("bytes_out") == new_value:
            return working
        working["bytes_out"] = new_value
    return working


__all__ = ["finalize_envelope_bytes_out"]
