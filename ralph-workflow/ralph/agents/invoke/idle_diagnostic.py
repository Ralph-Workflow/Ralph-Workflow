from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

type IdleDiagnosticValue = str | int | float | bool | list[object] | dict[str, object]


def _idle_diagnostic_value(item: object) -> IdleDiagnosticValue | None:
    match item:
        case bool() | int() | float() | str():
            return item
        case list():
            return list(item)
        case dict():
            return {str(key): value for key, value in item.items()}
        case _:
            return None


def as_idle_diagnostic(value: Mapping[str, object]) -> dict[str, IdleDiagnosticValue]:
    result: dict[str, IdleDiagnosticValue] = {}
    for key, item in value.items():
        converted = _idle_diagnostic_value(item)
        if converted is not None:
            result[key] = converted
    return result


__all__ = ["IdleDiagnosticValue", "as_idle_diagnostic"]
