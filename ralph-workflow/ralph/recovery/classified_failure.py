"""Structured classified failure model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .failure_category import FailureCategory


@dataclass(frozen=True)
class ClassifiedFailure:
    """A failure with its category, attribution, and budget-counting decision."""

    category: FailureCategory
    reason: str
    attributed_agent: str | None
    attributed_phase: str
    counts_against_budget: bool
    original_exception: BaseException | None
    raw_message: str
    reset_session: bool = field(default=False)
    # When True, the next recovery attempt should call
    # `RestartAwareMcpBridge.reset_tool_registry()` to rebuild the
    # visible tool list. Set by the failure classifier when the live
    # MCP server reports a missing tool via the
    # "No such tool available: mcp__<server>__<tool>" string (the
    # post-tool-result wedge failure mode) or when a runtime
    # `ToolDispatchError` is raised with an "is not registered" message.
    reset_tool_registry: bool = field(default=False)
    # When True, the agent is considered temporarily unavailable (e.g. out
    # of credits) and the recovery controller should skip it with exponential
    # backoff instead of retrying immediately.
    is_unavailable: bool = field(default=False)
    watchdog_reason: str | None = field(default=None)
