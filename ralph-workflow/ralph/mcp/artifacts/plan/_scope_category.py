"""Closed-enum category for plan scope items."""

from __future__ import annotations

from typing import Literal

ScopeCategory = Literal[
    "bugfix",
    "feature",
    "refactor",
    "test",
    "docs",
    "infra",
    "migration",
    "security",
    "performance",
    "cleanup",
    "research",
    "unknown",
    "file_change",
    "prompt",
    "other",
]

__all__ = ["ScopeCategory"]
