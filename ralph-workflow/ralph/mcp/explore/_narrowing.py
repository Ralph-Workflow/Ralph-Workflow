from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TypeGuard


def is_int_or_float(value: object) -> TypeGuard[int | float]:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float)


def is_str_or_bytes(value: object) -> TypeGuard[str | bytes | bytearray]:
    if isinstance(value, str):
        return True
    if isinstance(value, bytes):
        return True
    return isinstance(value, bytearray)


def is_str_or_number(value: object) -> TypeGuard[str | int | float]:
    if isinstance(value, str):
        return True
    return is_int_or_float(value)


def is_list_or_tuple(value: object) -> TypeGuard[list[object] | tuple[object, ...]]:
    if isinstance(value, list):
        return True
    return isinstance(value, tuple)


def as_sqlite_rows(raw_rows: Sequence[object]) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for item in raw_rows:
        if not isinstance(item, sqlite3.Row):
            msg = f"expected sqlite3.Row, got {type(item).__name__}"
            raise TypeError(msg)
        rows.append(item)
    return rows


def coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    msg = f"expected int-compatible value, got {type(value).__name__}"
    raise TypeError(msg)


def coerce_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return float(value)
    msg = f"expected float-compatible value, got {type(value).__name__}"
    raise TypeError(msg)


__all__ = [
    "as_sqlite_rows",
    "coerce_float",
    "coerce_int",
    "is_int_or_float",
    "is_list_or_tuple",
    "is_str_or_bytes",
    "is_str_or_number",
]
