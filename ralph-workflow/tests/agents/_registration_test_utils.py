"""Helpers for registration tests that mutate global parser/strategy registries.

Tests using register_agent_support temporarily modify module-level lookup
registries. This module saves and restores those registries so the mutations do
not leak between tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.parsers import _PARSER_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def _isolated_registries() -> Iterator[None]:
    """Save parser and strategy registries, restore them after the block."""
    original_parsers = dict(_PARSER_REGISTRY)
    original_strategies = dict(_STRATEGY_DISPATCH)
    try:
        yield
    finally:
        _PARSER_REGISTRY.clear()
        _PARSER_REGISTRY.update(original_parsers)
        _STRATEGY_DISPATCH.clear()
        _STRATEGY_DISPATCH.update(original_strategies)
