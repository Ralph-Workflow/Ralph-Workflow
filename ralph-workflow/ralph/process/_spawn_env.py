"""Sanitize inherited environment variables that harm descendant processes."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import MutableMapping

MALLOC_DEBUG_NOISE_VARS: tuple[str, ...] = (
    "MallocStackLogging",
    "MallocStackLoggingNoCompact",
)


def strip_malloc_debug_noise(env: MutableMapping[str, str]) -> tuple[str, ...]:
    """Remove inherited macOS malloc-stack-logging toggles from ``env``."""
    return tuple(name for name in MALLOC_DEBUG_NOISE_VARS if env.pop(name, None) is not None)


def sanitize_process_environment() -> tuple[str, ...]:
    """Remove malloc-debug toggles before this process launches descendants."""
    return strip_malloc_debug_noise(os.environ)
