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


def _is_sqlite_row(value: object) -> TypeGuard[sqlite3.Row]:
    return type(value).__name__ == "Row" and type(value).__module__ == "sqlite3"


def as_sqlite_rows(raw_rows: object) -> list[sqlite3.Row]:
    if isinstance(raw_rows, str):
        msg = f"expected Sequence of sqlite3.Row, got {type(raw_rows).__name__}"
        raise TypeError(msg)
    if isinstance(raw_rows, bytes):
        msg = f"expected Sequence of sqlite3.Row, got {type(raw_rows).__name__}"
        raise TypeError(msg)
    if isinstance(raw_rows, bytearray):
        msg = f"expected Sequence of sqlite3.Row, got {type(raw_rows).__name__}"
        raise TypeError(msg)
    if not isinstance(raw_rows, Sequence):
        msg = f"expected Sequence of sqlite3.Row, got {type(raw_rows).__name__}"
        raise TypeError(msg)
    rows: list[sqlite3.Row] = []
    for item in raw_rows:
        if not _is_sqlite_row(item):
            msg = f"expected sqlite3.Row, got {type(item).__name__}"
            raise TypeError(msg)
        rows.append(item)
    return rows


def rows_from_cursor(cur: sqlite3.Cursor) -> list[sqlite3.Row]:
    raw: object = cur.fetchall()
    return as_sqlite_rows(raw)


def rows_from_fetchmany(cur: sqlite3.Cursor, size: int) -> list[sqlite3.Row]:
    raw: object = cur.fetchmany(size)
    return as_sqlite_rows(raw)


def fetch_all_rows(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return rows_from_cursor(cursor)


def fetch_many_rows(cursor: sqlite3.Cursor, size: int) -> list[sqlite3.Row]:
    return rows_from_fetchmany(cursor, size)


def fetch_one_row(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    row: object = cursor.fetchone()
    if row is None:
        return None
    if not _is_sqlite_row(row):
        msg = f"expected sqlite3.Row, got {type(row).__name__}"
        raise TypeError(msg)
    return row


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
    "fetch_all_rows",
    "fetch_many_rows",
    "fetch_one_row",
    "is_int_or_float",
    "is_list_or_tuple",
    "is_str_or_bytes",
    "is_str_or_number",
    "rows_from_cursor",
    "rows_from_fetchmany",
]
