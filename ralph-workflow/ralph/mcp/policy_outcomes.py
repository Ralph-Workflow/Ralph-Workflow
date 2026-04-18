"""Shared helpers for interpreting MCP approval outcomes."""

from __future__ import annotations

from typing import cast

APPROVED_POLICY_OUTCOMES = frozenset({"approved", "allow", "allowed"})


def _attribute_value(
    obj: object, attribute_name: str, default: object | None = None
) -> object | None:
    return cast("object | None", getattr(obj, attribute_name, default))


def is_policy_approved(outcome: object | None) -> bool:
    if outcome is True:
        return True
    if isinstance(outcome, str):
        return outcome.strip().lower() in APPROVED_POLICY_OUTCOMES

    if isinstance(outcome, dict):
        for attribute_name in ("name", "value", "status"):
            attribute = outcome.get(attribute_name)
            if isinstance(attribute, str) and attribute.strip().lower() in APPROVED_POLICY_OUTCOMES:
                return True
        return False

    for attribute_name in ("name", "value", "status"):
        attribute = _attribute_value(outcome, attribute_name)
        if isinstance(attribute, str) and attribute.strip().lower() in APPROVED_POLICY_OUTCOMES:
            return True
    return False
