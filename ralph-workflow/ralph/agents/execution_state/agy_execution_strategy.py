"""Execution strategy for Google Anti Gravity (AGY) agents."""

from __future__ import annotations

from ._completion_mixin import CompletionEnforcingStrategy
from .generic_execution_strategy import GenericExecutionStrategy


class AgyExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
    """AGY strategy: completion evidence still required."""
