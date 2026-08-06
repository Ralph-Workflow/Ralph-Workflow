"""Data carrier for raw-log corruption breaks (S-8 / C4 / DoD 15).

A :class:`RawLogBreak` is the operator-facing record a consumer should
surface when it reads a raw ``.agent/raw/<id>.log`` back and finds
the file unreadable as JSONL. The brief treats the raw log as an
evidence source throughout: a transcript that can be silently
truncated or overwritten mid-run cannot support ``TRANSCRIPT``
provenance at all.

The two break shapes the 2026-08-06 captured run actually exhibited
are:

- ``NUL_BYTES``: a NUL-byte run, the byte-level fingerprint of a
  shared-pathname writer truncation (the first writer's ``"wb"``
  open truncates the second writer's already-written bytes, leaving
  the resulting file unparseable as JSONL past the hole).
- ``NON_JSONL``: a line that is not a JSON object, the fingerprint
  of rendered display output being written into the verbatim
  capture (the second writer was appending ``\u2713 PASS\u2026`` /
  ``\u2139 INFO\u2026`` style rendered text into the raw path).
- ``READ_ERROR``: the file could not be read at all (locked, missing
  parent). The detail names the OSError so the operator sees the
  I/O failure rather than a silent empty result.

``offset`` is the byte offset of the first byte that fails parsing.
``detail`` names the line and the parse failure so an operator can
locate the break on disk.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RawLogBreak:
    """One detected corruption break in a raw log file."""

    kind: str
    offset: int
    detail: str


__all__ = ["RawLogBreak"]
