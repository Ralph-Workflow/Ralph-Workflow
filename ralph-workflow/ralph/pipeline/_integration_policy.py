from __future__ import annotations

from dataclasses import dataclass

__all__ = ["IntegrationPolicy"]


@dataclass(frozen=True, slots=True)
class IntegrationPolicy:
    allow_nothing_to_do: bool = True
    allow_fast_forward: bool = True
    allow_rebase: bool = True
    allow_merge: bool = True
    allow_direct_integration: bool = True
    preserve_shared_history: bool = True
