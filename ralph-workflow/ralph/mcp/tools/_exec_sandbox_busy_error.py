"""Typed error raised when a reusable exec sandbox slot is already in use."""

from __future__ import annotations


class ExecSandboxBusyError(RuntimeError):
    """Raised when a reusable sandbox is already in use by another caller."""


__all__ = ["ExecSandboxBusyError"]
