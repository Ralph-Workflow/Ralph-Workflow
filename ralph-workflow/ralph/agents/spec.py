r"""The single declarative home for the headless-vs-interactive axis.

Replaces the legacy ``interactive=True`` flag and the magic
``session_flag='--resume {}'`` default in registration.py:142-145.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from ralph.config.agent_config import AgentConfig


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Captures the headless-vs-interactive axis declaratively.

    Attributes:
        name: Agent name (lowercased by AgentSupport).
        transport: Transport enum value.
        interactive: Whether this is an interactive (PTY) agent.
        requires_pty: Whether the agent requires a PTY. Only meaningful
            when ``interactive=True``.
        session_resume_template: Template string for session continuation,
            e.g. ``--resume {}``. Requires ``completion_required=True``.
        completion_required: Whether the agent requires explicit completion
            signals before a clean exit is terminal.
        subagent_capable: Whether the agent exposes usable sub-agent tooling.
    """

    name: str
    transport: AgentTransport
    interactive: bool = False
    requires_pty: bool = False
    session_resume_template: str | None = None
    completion_required: bool = False
    subagent_capable: bool = False

    def __post_init__(self) -> None:
        if self.requires_pty and not self.interactive:
            raise ValueError("requires_pty=True requires interactive=True")
        if self.session_resume_template is not None and not self.completion_required:
            raise ValueError(
                "session_resume_template requires completion_required=True"
            )

    @classmethod
    def from_agent_config(
        cls,
        config: AgentConfig,
        *,
        interactive: bool = False,
        completion_required: bool = False,
    ) -> AgentSpec:
        """Build an AgentSpec from an AgentConfig plus keyword overrides."""
        return cls(
            name=config.cmd,
            transport=config.transport or AgentTransport.GENERIC,
            interactive=interactive,
            requires_pty=interactive,
            session_resume_template=config.session_flag,
            completion_required=completion_required,
            subagent_capable=config.subagent_capability or False,
        )
