"""Tests for the consolidated strict/lenient plan-payload decoders.

The four previously-duplicated JSON parsers that lived in
``tools/artifact.py``, ``prompts/plan_format.py``, and the original
``__init__.py`` now share a private ``_decode_plan_payload`` core
exposed through two public helpers:

  - ``parse_plan_payload_strict``: raises ``PlanArtifactValidationError``
    on any failure.
  - ``parse_plan_payload_lenient``: returns ``None`` on the same
    failures (used by ``extract_plan_payload`` /
    ``_parse_plan_content`` which are None-tolerant).

These tests cover BOTH helpers against the same canonical cases so
envelope-aware decoding stays in one place.
"""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.plan._validation import (
    PlanArtifactValidationError,
    parse_plan_payload_lenient,
    parse_plan_payload_strict,
)


def _bare_payload() -> dict[str, object]:
    return {"summary": {"intent": "x"}, "steps": []}


def _envelope_payload() -> dict[str, object]:
    return {"type": "plan", "content": _bare_payload()}


def test_parse_plan_payload_strict_bare_dict() -> None:
    payload = _bare_payload()
    decoded = parse_plan_payload_strict(payload)
    assert decoded == payload


def test_parse_plan_payload_lenient_bare_dict() -> None:
    payload = _bare_payload()
    decoded = parse_plan_payload_lenient(payload)
    assert decoded == payload


def test_parse_plan_payload_strict_envelope_dict() -> None:
    decoded = parse_plan_payload_strict(_envelope_payload())
    assert decoded == _bare_payload()


def test_parse_plan_payload_lenient_envelope_dict() -> None:
    decoded = parse_plan_payload_lenient(_envelope_payload())
    assert decoded == _bare_payload()


def test_parse_plan_payload_strict_invalid_json_raises() -> None:
    with pytest.raises(PlanArtifactValidationError):
        parse_plan_payload_strict("{not json")


def test_parse_plan_payload_lenient_invalid_json_returns_none() -> None:
    assert parse_plan_payload_lenient("{not json") is None


def test_parse_plan_payload_strict_envelope_with_non_dict_content_raises() -> None:
    with pytest.raises(PlanArtifactValidationError):
        parse_plan_payload_strict({"type": "plan", "content": "not-a-dict"})


def test_parse_plan_payload_lenient_envelope_with_non_dict_content_returns_none() -> None:
    assert parse_plan_payload_lenient({"type": "plan", "content": "not-a-dict"}) is None
