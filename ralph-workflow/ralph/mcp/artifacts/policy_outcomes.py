from __future__ import annotations

from ralph._typed_attrs import read_attr

APPROVED_POLICY_OUTCOMES = frozenset({"approved", "allow", "allowed"})


def _attribute_value(
    obj: object, attribute_name: str, default: object | None = None
) -> object | None:
    if attribute_name == "name":
        return read_attr(obj, "name", default)
    if attribute_name == "value":
        return read_attr(obj, "value", default)
    if attribute_name == "status":
        return read_attr(obj, "status", default)
    return default


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
