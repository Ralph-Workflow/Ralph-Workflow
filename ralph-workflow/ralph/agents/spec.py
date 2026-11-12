from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.config.agent_config import AgentConfig
    from ralph.config.enums import AgentTransport


@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    transport: AgentTransport
    interactive: bool = False
    requires_pty: bool = False
    session_resume_template: str | None = None
    completion_required: bool = False
    subagent_capable: bool = False
    no_default_session_flag: bool = False

    def __post_init__(self) -> None:
        if self.requires_pty and not self.interactive:
            raise ValueError("requires_pty=True requires interactive=True")
        if self.session_resume_template is not None and not self.completion_required:
            raise ValueError("session_resume_template requires completion_required=True")
        if self.no_default_session_flag and self.session_resume_template is not None:
            raise ValueError(
                "no_default_session_flag=True is inconsistent with an explicit "
                "session_resume_template; pick one or the other"
            )

    @classmethod
    def from_agent_config(
        cls,
        config: AgentConfig,
        *,
        interactive: bool = False,
        completion_required: bool = False,
        no_default_session_flag: bool = False,
    ) -> AgentSpec:
        return cls(
            name=config.cmd,
            transport=cast("AgentTransport", config.transport),
            interactive=interactive,
            requires_pty=interactive,
            session_resume_template=config.session_flag,
            completion_required=completion_required,
            subagent_capable=config.subagent_capability or False,
            no_default_session_flag=no_default_session_flag,
        )
