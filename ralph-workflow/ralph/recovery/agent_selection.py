"""Pure priority-order agent selection for recovery fallover."""

from collections.abc import Sequence
from dataclasses import dataclass

type AgentAvailability = tuple[str, bool, int, bool]


@dataclass(frozen=True, slots=True)
class AgentSelection:
    """The selected agent and the reason each other agent was skipped."""

    index: int | None
    agent: str | None
    skipped_reasons: tuple[tuple[str, str], ...]


def agent_availability(
    agent: str,
    available: bool,
    cooldown_ms_remaining: int,
    spent: bool,
) -> AgentAvailability:
    """Create selection-relevant availability state for one agent."""
    return agent, available, cooldown_ms_remaining, spent


def select_preferred_agent(rows: Sequence[AgentAvailability]) -> AgentSelection:
    """Return the lowest-index agent that is available and has allowance left."""
    selected_index = next(
        (
            index
            for index, (_agent, available, cooldown_ms_remaining, spent) in enumerate(rows)
            if available and cooldown_ms_remaining <= 0 and not spent
        ),
        None,
    )
    skipped_reasons: list[tuple[str, str]] = []
    for index, (agent, available, cooldown_ms_remaining, spent) in enumerate(rows):
        if index == selected_index:
            continue
        if not available or cooldown_ms_remaining > 0:
            skipped_reasons.append(
                (agent, f"cooldown ({cooldown_ms_remaining}ms remaining)")
            )
        elif spent:
            skipped_reasons.append((agent, "spent"))
        else:
            skipped_reasons.append((agent, "lower_priority"))
    selected_agent = rows[selected_index][0] if selected_index is not None else None
    return AgentSelection(selected_index, selected_agent, tuple(skipped_reasons))


def format_selection_evidence(selection: AgentSelection) -> str:
    """Format a selection decision as one operator-readable transcript line."""
    skipped = "; ".join(
        f"{agent}: {reason}" for agent, reason in selection.skipped_reasons
    )
    if selection.agent is None:
        return f"No selectable agent (skipped {skipped})" if skipped else "No selectable agent"
    return (
        f"Selected agent {selection.agent} (skipped {skipped})"
        if skipped
        else f"Selected agent {selection.agent}"
    )
